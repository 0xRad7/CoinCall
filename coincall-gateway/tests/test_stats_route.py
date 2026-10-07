"""unit：GET /internal/stats/calls——自有 calls/settle_queue 只读聚合（P1-3 活跃度数据源）。

口径：calls_success = success + settled（计费成功）；settle_pending 按 call_id 回连
calls.service_id 归属服务；只读自有 DuckDB，不改冻结契约文件（A4）。
"""

import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.modules.calls import CallRecord, CallStatus, SettleStatus
from tests.conftest import gateway_serve

pytestmark = pytest.mark.unit

WALLET = "0x1234567890AbCdEf1234567890aBcDeF12345678"


def _seed(
    app: FastAPI,
    rows: list[tuple[str, str, str]],
    settles: list[tuple[str, str]] | None = None,
) -> None:
    """rows: (call_id, service_id, calls.status)；settles: (call_id, settle_queue.status)。"""
    for call_id, service_id, status in rows:
        app.state.store.insert_call(
            CallRecord(
                call_id=call_id,
                service_id=service_id,
                provider_agent_id=137,
                consumer_key_id="key_x",
                consumer_wallet=WALLET,
                amount_raw=10000,
            )
        )
        app.state.store.mark_call(call_id, CallStatus(status))
    for call_id, status in settles or []:
        app.state.store.insert_settle(
            call_id=call_id, provider_token_id=137, auth={"from": "0xf", "value": "1"}
        )
        if status != "pending":
            app.state.store.mark_settle(call_id, SettleStatus(status))


async def test_stats_per_service_and_totals(settings: Settings) -> None:
    async with gateway_serve(settings) as (client, app):
        _seed(
            app,
            [
                ("c1", "svc_a", "success"),
                ("c2", "svc_a", "settled"),
                ("c3", "svc_a", "aborted"),
                ("c4", "svc_a", "bad_debt"),
                ("c5", "svc_b", "success"),
                ("c6", "svc_b", "inflight"),
            ],
            settles=[("c2", "done"), ("c5", "pending"), ("c6", "pending")],
        )
        resp = await client.get("/internal/stats/calls")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        by_id = {row["service_id"]: row for row in body["services"]}
        assert set(by_id) == {"svc_a", "svc_b"}
        a = by_id["svc_a"]
        assert a["calls_success"] == 2  # success + settled（计费成功口径）
        assert a["calls_settled"] == 1
        assert a["calls_aborted"] == 1
        assert a["bad_debt"] == 1
        assert a["settle_pending"] == 0
        assert a["last_call_at"]
        b = by_id["svc_b"]
        assert b["calls_success"] == 1
        assert b["calls_inflight"] == 1
        assert b["settle_pending"] == 2  # c5/c6 的 pending 归属 svc_b
        totals = body["totals"]
        assert totals["calls_success"] == 3
        assert totals["calls_settled"] == 1
        assert totals["calls_aborted"] == 1
        assert totals["bad_debt"] == 1
        assert totals["settle_pending"] == 2
        assert body["generated_at"]


async def test_stats_empty_db_graceful(settings: Settings) -> None:
    async with gateway_serve(settings) as (client, _app):
        resp = await client.get("/internal/stats/calls")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["services"] == []
        assert body["totals"]["calls_success"] == 0
        assert body["totals"]["last_call_at"] is None


async def test_stats_success_only_when_all_aborted(settings: Settings) -> None:
    async with gateway_serve(settings) as (client, app):
        _seed(app, [("c1", "svc_a", "aborted"), ("c2", "svc_a", "aborted")])
        body = (await client.get("/internal/stats/calls")).json()
        assert body["services"][0]["calls_success"] == 0
        assert body["services"][0]["calls_aborted"] == 2
        assert body["services"][0]["last_call_at"]


# ---- 10 §1/§2 冻结契约：窗口化扩展统计（p50/p95/distinct_payers/last_activity_at） ----

WALLET_A = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
WALLET_B = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
WALLET_C = "0x000000000000000000000000000000000000dEaD"


def _seed_window(
    app: FastAPI,
    rows: list[tuple[str, str, str, str, int | None]],
    age_hours: dict[str, float] | None = None,
) -> None:
    """rows: (call_id, service_id, status, consumer_wallet, latency_ms)。"""
    for call_id, service_id, status, wallet, latency in rows:
        app.state.store.insert_call(
            CallRecord(
                call_id=call_id,
                service_id=service_id,
                provider_agent_id=137,
                consumer_key_id="key_x",
                consumer_wallet=wallet,
                amount_raw=10000,
            )
        )
        app.state.store.mark_call(call_id, CallStatus(status), http_status=200, latency_ms=latency)
        if age_hours and call_id in age_hours:
            app.state.store.conn.execute(
                "UPDATE calls SET created_at = now() - to_hours(?) WHERE call_id = ?",
                [age_hours[call_id], call_id],
            )


async def test_stats_window_default_168_and_fields(settings: Settings) -> None:
    """默认窗口 168h：p50/p95=quantile_cont、distinct_payers 只数 success/settled。"""
    async with gateway_serve(settings) as (client, app):
        _seed_window(
            app,
            [
                ("c1", "svc_a", "success", WALLET_A, 100),
                ("c2", "svc_a", "settled", WALLET_A, 200),
                ("c3", "svc_a", "success", WALLET_B, 300),
                ("c4", "svc_a", "success", WALLET_B, 400),
                ("c5", "svc_a", "aborted", WALLET_C, None),  # 坏样本不入分位/付款人
                ("c6", "svc_a", "inflight", WALLET_C, None),  # NULL 延迟不影响分位
            ],
        )
        resp = await client.get("/internal/stats/calls")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        row = body["services"][0]
        assert row["window_hours"] == 168
        assert row["calls_success"] == 4
        assert row["p50_ms"] == 250  # quantile_cont([100,200,300,400], .5)
        assert row["p95_ms"] == 385  # quantile_cont([...], .95)
        assert row["distinct_payers"] == 2  # A/B；aborted 的 C 不计
        assert row["last_activity_at"]
        # totals：池化分位（非逐行求和）与池化去重
        totals = body["totals"]
        assert totals["p50_ms"] == 250
        assert totals["p95_ms"] == 385
        assert totals["distinct_payers"] == 2
        assert totals["window_hours"] == 168


async def test_stats_window_excludes_old_rows(settings: Settings) -> None:
    async with gateway_serve(settings) as (client, app):
        _seed_window(
            app,
            [
                ("fresh", "svc_a", "success", WALLET_A, 100),
                ("old", "svc_a", "success", WALLET_B, 999),
            ],
            age_hours={"fresh": 2, "old": 200},  # 200h 前落在默认 168h 窗口外
        )
        body = (await client.get("/internal/stats/calls?window_hours=168")).json()
        row = body["services"][0]
        assert row["calls_success"] == 1
        assert row["distinct_payers"] == 1
        assert row["p50_ms"] == 100  # 窗口外 999 不进分位
        # 窗口放大到 720h（上限）覆盖旧行
        body720 = (await client.get("/internal/stats/calls?window_hours=720")).json()
        row720 = body720["services"][0]
        assert row720["calls_success"] == 2
        assert row720["distinct_payers"] == 2
        assert row720["window_hours"] == 720


async def test_stats_window_validation(settings: Settings) -> None:
    async with gateway_serve(settings) as (client, _app):
        assert (await client.get("/internal/stats/calls?window_hours=24")).status_code == 200
        assert (await client.get("/internal/stats/calls?window_hours=0")).status_code == 422
        assert (await client.get("/internal/stats/calls?window_hours=721")).status_code == 422
        assert (await client.get("/internal/stats/calls?window_hours=abc")).status_code == 422


async def test_stats_window_totals_distinct_is_union_not_sum(settings: Settings) -> None:
    """同一钱包跨服务付费：totals.distinct_payers 是并集去重，不是逐服务相加。"""
    async with gateway_serve(settings) as (client, app):
        _seed_window(
            app,
            [
                ("c1", "svc_a", "success", WALLET_A, 100),
                ("c2", "svc_b", "success", WALLET_A, 300),
                ("c3", "svc_b", "success", WALLET_B, 500),
            ],
        )
        body = (await client.get("/internal/stats/calls")).json()
        by_id = {row["service_id"]: row for row in body["services"]}
        assert by_id["svc_a"]["distinct_payers"] == 1
        assert by_id["svc_b"]["distinct_payers"] == 2
        assert body["totals"]["distinct_payers"] == 2  # A 跨服务只数一次


async def test_stats_window_empty_latency_fields_none(settings: Settings) -> None:
    async with gateway_serve(settings) as (client, app):
        _seed_window(app, [("c1", "svc_a", "aborted", WALLET_A, None)])
        body = (await client.get("/internal/stats/calls")).json()
        row = body["services"][0]
        assert row["calls_success"] == 0
        assert row["p50_ms"] is None
        assert row["p95_ms"] is None
        assert row["distinct_payers"] == 0
        assert row["last_activity_at"] is not None  # aborted 也是活动
