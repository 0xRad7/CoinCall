"""决策层（10 篇）：四分量证据包排序 + 类目分区 + as_of 时间注入 + 锚定 pending/result。

分量口径（§0.5 本轮裁决）：
- revenue   = 0.5*norm(ln(total_raw+1)) + 0.5*norm(distinct_payers)（total=链上 Charged 全量）
- fulfillment = success_rate * latency_bonus(p95)（≤2s 满分、≥10s 零分线性）
- feedback  = norm(bayesian_avg)（先验=全局均值，m=10）
- freshness = exp(-ln2*Δh/48)（Δh=as_of−last_activity，无活动=0）
"""

import copy
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import (
    VALID_MANIFEST,
    FakeChainClient,
    FakeGatewayStatsClient,
    FakeIdentityClient,
    FakeReceiptPubkeyClient,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 1, tzinfo=UTC)

W_A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
W_B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
W_C = "0xcccccccccccccccccccccccccccccccccccccccc"
PAYER = "0xdddddddddddddddddddddddddddddddddddddddd"

WEIGHTS = {"revenue": 0.5, "fulfillment": 0.3, "freshness": 0.2}


def _client(
    tmp_path: Path,
    *,
    gateway: FakeGatewayStatsClient | None = None,
) -> TestClient:
    settings = Settings(duckdb_path=str(tmp_path / "dec.duckdb"))
    app = create_app(
        settings,
        identity_client=FakeIdentityClient(),
        chain_client=FakeChainClient(),
        gateway_client=gateway or FakeGatewayStatsClient(),
        receipt_key_client=FakeReceiptPubkeyClient(),
    )
    return TestClient(app)


def _manifest(
    sid: str,
    agent_id: int,
    wallet: str,
    *,
    category: str | None = None,
    amount: str = "0.01",
    amount_raw: str = "10000",
) -> dict[str, Any]:
    manifest = copy.deepcopy(VALID_MANIFEST)
    manifest["service_id"] = sid
    manifest["provider"] = {
        "agent_id": agent_id,
        "wallet": wallet,
        "display_name": f"provider-{agent_id}",
    }
    manifest["pricing"] = {"model": "per_call", "amount": amount, "amount_raw": amount_raw}
    if category is not None:
        manifest["category"] = category
    return manifest


def _stats_row(
    sid: str,
    *,
    success: int = 2,
    settled: int | None = None,
    aborted: int = 0,
    p50: int = 800,
    p95: int = 1000,
    distinct: int = 1,
    last_activity: datetime | None = None,
) -> dict[str, Any]:
    return {
        "service_id": sid,
        "calls_success": success,
        "calls_settled": settled if settled is not None else success,
        "calls_aborted": aborted,
        "bad_debt": 0,
        "p50_ms": p50,
        "p95_ms": p95,
        "distinct_payers": distinct,
        "last_activity_at": (last_activity.isoformat() if last_activity is not None else None),
        "window_hours": 168,
    }


def _seed_charged(store: Any, wallet: str, value_raw: int, *, n: int = 1) -> None:
    for i in range(n):
        store.insert_charged_events(
            [
                {
                    "tx_hash": f"0x{wallet[2:10]}{i:056x}",
                    "log_index": 0,
                    "block_number": 100 + i,
                    "provider": wallet.lower(),
                    "payer": PAYER,
                    "value_raw": value_raw,
                    "nonce": "0x" + "00" * 31 + "01",
                }
            ]
        )


def _seed_feedback(store: Any, sid: str, ratings: list[int]) -> None:
    for i, rating in enumerate(ratings):
        assert store.claim_feedback(f"rcp_seed_{sid}_{i}", sid, rating, None), "种子反馈不可重复"


class TestCategories:
    def test_categories_vocabulary_and_counts(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            assert (
                client.post(
                    "/manifests", json=_manifest("svc_t1", 137, W_A, category="translation")
                ).status_code
                == 201
            )
            assert (
                client.post(
                    "/manifests", json=_manifest("svc_t2", 138, W_B, category="translation")
                ).status_code
                == 201
            )
            paused = _manifest("svc_t3", 139, W_C, category="translation")
            paused["status"] = "paused"
            assert client.post("/manifests", json=paused).status_code == 201
            assert (
                client.post(
                    "/manifests", json=_manifest("svc_d1", 140, W_C, category="data-feed")
                ).status_code
                == 201
            )
            assert (
                client.post("/manifests", json=_manifest("svc_o1", 141, W_B)).status_code == 201
            )  # 缺省 other

            body = client.get("/decision/categories").json()
            assert body["categories"] == [
                "translation",
                "data-feed",
                "on-chain-query",
                "analysis",
                "agent-tool",
                "other",
            ]
            # paused 不计入（按 active manifests）
            assert body["counts"] == {
                "translation": 2,
                "data-feed": 1,
                "on-chain-query": 0,
                "analysis": 0,
                "agent-tool": 0,
                "other": 1,
            }
            assert body["total_active"] == 4


class TestDecisionServices:
    def test_weights_and_formula_self_described(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            assert client.post("/manifests", json=_manifest("svc_t1", 137, W_A)).status_code == 201
            body = client.get("/decision/services").json()
            assert body["weights"] == WEIGHTS
            assert "0.5*revenue" in body["formula"] and "norm" in body["formula"]
            assert body["window_hours"] == 168
            assert body["sort"] == "score"
            assert body["as_of"]  # 默认 now
            row = body["services"][0]
            assert row["service_id"] == "svc_t1"
            assert row["category"] == "other"
            assert row["tags"] == []
            assert row["components"]["revenue"]["proof"] == f"/leaderboard/providers/{W_A}/proof"

    def test_category_filter_and_unknown_422(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            assert (
                client.post(
                    "/manifests", json=_manifest("svc_t1", 137, W_A, category="translation")
                ).status_code
                == 201
            )
            assert (
                client.post(
                    "/manifests", json=_manifest("svc_a1", 138, W_B, category="analysis")
                ).status_code
                == 201
            )
            body = client.get("/decision/services", params={"category": "translation"}).json()
            assert [r["service_id"] for r in body["services"]] == ["svc_t1"]
            # 空 category = 全量（分区未指定）
            assert (
                len(client.get("/decision/services", params={"category": ""}).json()["services"])
                == 2
            )
            resp = client.get("/decision/services", params={"category": "nonsense"})
            assert resp.status_code == 422
            assert resp.json()["code"] == "unknown_category"

    def test_revenue_component_norm_log_and_distinct(self, tmp_path: Path) -> None:
        """rev = 0.5*norm(ln(total+1)) + 0.5*norm(distinct)；distinct 等值时零区间=0.5。"""
        gateway = FakeGatewayStatsClient(
            stats={
                "services": [
                    _stats_row("svc_rich", distinct=1),
                    _stats_row("svc_poor", distinct=1),
                ],
                "totals": {},
            }
        )
        with _client(tmp_path, gateway=gateway) as client:
            assert (
                client.post("/manifests", json=_manifest("svc_rich", 137, W_A)).status_code == 201
            )
            assert (
                client.post("/manifests", json=_manifest("svc_poor", 138, W_B)).status_code == 201
            )
            _seed_charged(client.app.state.store, W_A, 11000)
            _seed_charged(client.app.state.store, W_B, 1000)
            body = client.get("/decision/services").json()
            by_id = {r["service_id"]: r for r in body["services"]}
            rich, poor = (
                by_id["svc_rich"]["components"]["revenue"],
                by_id["svc_poor"]["components"]["revenue"],
            )
            assert rich["total_raw"] == 11000
            assert rich["distinct_payers"] == 1
            assert poor["distinct_payers"] == 1
            # ln 归一化后 rich=1、poor=0；distinct 两值相等 → 零区间 0.5
            assert rich["score_component"] == pytest.approx(0.5 * 1.0 + 0.5 * 0.5)
            assert poor["score_component"] == pytest.approx(0.5 * 0.0 + 0.5 * 0.5)
            assert by_id["svc_rich"]["score"] > by_id["svc_poor"]["score"]

    def test_fulfillment_success_rate_and_latency_bonus(self, tmp_path: Path) -> None:
        """bonus 线性：≤2s=1、6s=0.5、≥10s=0；success_rate=(s+settled)/(s+settled+aborted)。"""
        gateway = FakeGatewayStatsClient(
            stats={
                "services": [
                    _stats_row("svc_fast", p95=1500),
                    _stats_row("svc_slow", p95=6000),
                    _stats_row("svc_dead", p95=12000),
                    _stats_row("svc_flaky", success=3, settled=3, aborted=1, p95=1000),
                ],
                "totals": {},
            }
        )
        with _client(tmp_path, gateway=gateway) as client:
            for i, sid in enumerate(["svc_fast", "svc_slow", "svc_dead", "svc_flaky"]):
                assert (
                    client.post("/manifests", json=_manifest(sid, 137 + i, W_A)).status_code == 201
                )
            body = client.get("/decision/services").json()
            by_id = {r["service_id"]: r for r in body["services"]}
            fast = by_id["svc_fast"]["components"]["fulfillment"]
            slow = by_id["svc_slow"]["components"]["fulfillment"]
            dead = by_id["svc_dead"]["components"]["fulfillment"]
            flaky = by_id["svc_flaky"]["components"]["fulfillment"]
            assert fast["score_component"] == pytest.approx(1.0)
            assert slow["score_component"] == pytest.approx(0.5)
            assert dead["score_component"] == pytest.approx(0.0)
            assert flaky["success_rate"] == pytest.approx((3 + 3) / (3 + 3 + 1))
            assert flaky["score_component"] == pytest.approx(6 / 7, abs=1e-3)
            assert fast["window_hours"] == 168
            assert fast["p50_ms"] == 800
            assert fast["proof"]["digest"].startswith("sha256:")

    def test_fulfillment_no_data_zero_contribution(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            assert (
                client.post("/manifests", json=_manifest("svc_ghost", 137, W_A)).status_code == 201
            )
            row = client.get("/decision/services").json()["services"][0]
            ful = row["components"]["fulfillment"]
            assert ful["success_rate"] is None
            assert ful["score_component"] is None  # 无数据不折算成 0 值证据，只在总分按 0 计
            assert row["components"]["freshness"]["score_component"] == 0.0  # 无活动=0

    def test_freshness_as_of_injection_flips_order(self, tmp_path: Path) -> None:
        """演示时间注入（10 §0.5）：同数据、不同 as_of → 新鲜度衰减改变排序。"""
        gateway = FakeGatewayStatsClient(
            stats={
                "services": [
                    _stats_row("svc_old", p95=6000, last_activity=T0 - timedelta(hours=300)),
                    _stats_row("svc_new", p95=500, last_activity=T0 - timedelta(hours=1)),
                ],
                "totals": {},
            }
        )
        with _client(tmp_path, gateway=gateway) as client:
            assert client.post("/manifests", json=_manifest("svc_old", 137, W_A)).status_code == 201
            assert client.post("/manifests", json=_manifest("svc_new", 138, W_B)).status_code == 201
            _seed_charged(client.app.state.store, W_A, 11000)
            _seed_charged(client.app.state.store, W_B, 10000)

            near = client.get("/decision/services", params={"as_of": T0.isoformat()}).json()
            assert [r["service_id"] for r in near["services"]] == ["svc_new", "svc_old"]
            fresh = near["services"][0]["components"]["freshness"]
            assert fresh["half_life_h"] == 48
            assert fresh["formula"] == "exp(-ln2*Δh/48)"
            assert fresh["score_component"] == pytest.approx(math.pow(2, -1 / 48), abs=1e-3)
            assert near["as_of"] == T0.isoformat()

            far = client.get(
                "/decision/services", params={"as_of": (T0 + timedelta(hours=2000)).isoformat()}
            ).json()
            assert [r["service_id"] for r in far["services"]] == ["svc_old", "svc_new"]

    def test_bad_as_of_422(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            resp = client.get("/decision/services", params={"as_of": "not-a-date"})
            assert resp.status_code == 422
            assert resp.json()["code"] == "bad_as_of"

    def test_sort_by_price(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            assert (
                client.post(
                    "/manifests",
                    json=_manifest("svc_cheap", 137, W_A, amount="0.01", amount_raw="10000"),
                ).status_code
                == 201
            )
            assert (
                client.post(
                    "/manifests",
                    json=_manifest("svc_premium", 138, W_B, amount="0.5", amount_raw="500000"),
                ).status_code
                == 201
            )
            body = client.get("/decision/services", params={"sort": "price"}).json()
            assert [r["service_id"] for r in body["services"]] == ["svc_premium", "svc_cheap"]
            assert body["sort"] == "price"
            assert body["services"][0]["price_raw"] == "500000"
            resp = client.get("/decision/services", params={"sort": "bogus"})
            assert resp.status_code == 422

    def test_window_hours_passed_to_gateway(self, tmp_path: Path) -> None:
        gateway = FakeGatewayStatsClient()
        with _client(tmp_path, gateway=gateway) as client:
            assert client.post("/manifests", json=_manifest("svc_t1", 137, W_A)).status_code == 201
            client.get("/decision/services", params={"window_hours": 24})
            assert gateway.requested_windows[-1] == 24
            client.get("/decision/services")
            assert gateway.requested_windows[-1] == 168

    def test_gateway_down_degrades_not_fails(self, tmp_path: Path) -> None:
        gateway = FakeGatewayStatsClient(error=RuntimeError("gw down"))
        with _client(tmp_path, gateway=gateway) as client:
            assert client.post("/manifests", json=_manifest("svc_t1", 137, W_A)).status_code == 201
            body = client.get("/decision/services").json()
            assert len(body["services"]) == 1
            assert any(d.startswith("gateway_stats_failed") for d in body["degraded"])
            assert body["services"][0]["components"]["fulfillment"]["success_rate"] is None


class TestExplain:
    def test_explain_unknown_404(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            resp = client.get("/decision/explain/svc_missing")
            assert resp.status_code == 404
            assert resp.json()["code"] == "service_not_found"


class TestAnchoring:
    def test_anchor_pending_and_result_idempotent(self, tmp_path: Path) -> None:
        gateway = FakeGatewayStatsClient(stats={"services": [_stats_row("svc_t1")], "totals": {}})
        with _client(tmp_path, gateway=gateway) as client:
            assert client.post("/manifests", json=_manifest("svc_t1", 137, W_A)).status_code == 201
            pending = client.get("/internal/decision/anchor-pending").json()
            rows = pending["pending"]
            assert len(rows) == 1
            row = rows[0]
            assert row["provider_agent_id"] == 137
            assert row["digest"].startswith("sha256:")
            svc = row["payload"]["services"]["svc_t1"]
            assert svc["fulfillment"]["calls_success"] == 2
            assert "feedback" not in svc  # 反馈层已移除（78c8974），锚定载荷不再含该键

            # 语义/形态校验失败 → 422
            bad_tx = client.post(
                "/internal/decision/anchor-result",
                json={"agent_id": 137, "digest": row["digest"], "tx_hash": "0xdead"},
            )
            assert bad_tx.status_code == 422
            stale = client.post(
                "/internal/decision/anchor-result",
                json={"agent_id": 137, "digest": "sha256:" + "0" * 64, "tx_hash": "0x" + "ab" * 32},
            )
            assert stale.status_code == 422
            unknown = client.post(
                "/internal/decision/anchor-result",
                json={"agent_id": 999, "digest": row["digest"], "tx_hash": "0x" + "ab" * 32},
            )
            assert unknown.status_code == 422

            ok = client.post(
                "/internal/decision/anchor-result",
                json={"agent_id": 137, "digest": row["digest"], "tx_hash": "0x" + "ab" * 32},
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["duplicate"] is False
            # 幂等：重复提交同一 (agent, digest) 不新增行
            again = client.post(
                "/internal/decision/anchor-result",
                json={"agent_id": 137, "digest": row["digest"], "tx_hash": "0x" + "ab" * 32},
            )
            assert again.status_code == 200
            assert again.json()["duplicate"] is True

            # 锚定后该 agent 退出 pending；explain 透出 anchor_tx
            assert client.get("/internal/decision/anchor-pending").json()["pending"] == []
            explain = client.get("/decision/explain/svc_t1").json()
            assert explain["components"]["fulfillment"]["proof"]["anchor_tx"] == "0x" + "ab" * 32

    def test_anchor_pending_gateway_down_still_lists(self, tmp_path: Path) -> None:
        gateway = FakeGatewayStatsClient(error=RuntimeError("gw down"))
        with _client(tmp_path, gateway=gateway) as client:
            assert client.post("/manifests", json=_manifest("svc_t1", 137, W_A)).status_code == 201
            pending = client.get("/internal/decision/anchor-pending").json()
            assert len(pending["pending"]) == 1
            svc = pending["pending"][0]["payload"]["services"]["svc_t1"]
            assert svc["fulfillment"]["available"] is False


def _ratings(avg: float, n: int) -> list[int]:
    """构造均值≈avg 的 n 条整型评分（1-5）。"""
    total = avg * n
    base = int(total // n)
    frac_count = round(total - base * n)
    ratings = [base + 1] * frac_count + [base] * (n - frac_count)
    assert len(ratings) == n
    return ratings
