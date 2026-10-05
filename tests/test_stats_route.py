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
