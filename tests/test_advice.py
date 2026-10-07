"""决策层 /advice 极简判定视图（design/decision-as-advice.md §3 方案一）：同引擎判定式投影。

覆盖口径：
- verb 五态：recommend（current 缺失/不在分区）→ keep（current 即榜首，含同分区唯一服务）
  → indifferent（与 current 分差 <0.05 死区）→ switch；insufficient_data（分区无 active
  服务或全无履约数据）
- reason 确定性模板（禁 LLM）：收入分差>0.2→"收入明显领先/落后"；success_rate 差>0.1→
  "履约 X% vs Y%"；p95 差>1000ms→"延迟快/慢 N ms"；age_h<1→"刚刚活跃"、<24→"N 小时前活跃"；
  current 相关另拼"较你选的 {current}"；信号段最多 3 段（三信号），" + "连接
- budget_impact：daily_budget_raw 提供时 "price / 日额 share"，缺省 null
- 同引擎一致性：advice.recommend == /decision/services 同分区榜首
"""

import copy
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


def _client(
    tmp_path: Path,
    *,
    gateway: FakeGatewayStatsClient | None = None,
) -> TestClient:
    settings = Settings(duckdb_path=str(tmp_path / "advice.duckdb"))
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
        "p50_ms": 800,
        "p95_ms": p95,
        "distinct_payers": distinct,
        "last_activity_at": (last_activity.isoformat() if last_activity is not None else None),
        "window_hours": 168,
    }


def _seed_charged(store: Any, wallet: str, value_raw: int) -> None:
    store.insert_charged_events(
        [
            {
                "tx_hash": "0x" + wallet[2:10] + "ab" * 30,
                "log_index": 0,
                "block_number": 100,
                "provider": wallet.lower(),
                "payer": PAYER,
                "value_raw": value_raw,
                "nonce": "0x" + "00" * 31 + "01",
            }
        ]
    )


def _switch_fixture(client: TestClient) -> None:
    """悬殊双服务（translation 分区）：收入/履约/延迟全维度领先 + 5 小时前活跃。

    svc_hi: rev=1.0（10 万 vs 1000 raw，distinct 3 vs 1）、rate 1.0 vs 0.5、p95 1000 vs 4000、
    age 5h vs 300h → 四条信号段全命中，reason 截断为前三段（freshness 被截）。
    """
    assert (
        client.post(
            "/manifests", json=_manifest("svc_hi", 137, W_A, category="translation")
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/manifests", json=_manifest("svc_lo", 138, W_B, category="translation")
        ).status_code
        == 201
    )
    _seed_charged(client.app.state.store, W_A, 100_000)
    _seed_charged(client.app.state.store, W_B, 1000)


def _switch_gateway() -> FakeGatewayStatsClient:
    return FakeGatewayStatsClient(
        stats={
            "services": [
                _stats_row(
                    "svc_hi",
                    success=2,
                    settled=0,
                    aborted=0,
                    p95=1000,
                    distinct=3,
                    last_activity=T0 - timedelta(hours=5),
                ),
                _stats_row(
                    "svc_lo",
                    success=1,
                    settled=0,
                    aborted=1,
                    p95=4000,
                    distinct=1,
                    last_activity=T0 - timedelta(hours=300),
                ),
            ],
            "totals": {},
        }
    )


class TestAdviceVerbs:
    def test_recommend_without_current(self, tmp_path: Path) -> None:
        """current 缺失 → 纯推荐；margin=与次名分差；budget_impact 缺省 null。"""
        with _client(tmp_path, gateway=_switch_gateway()) as client:
            _switch_fixture(client)
            body = client.get(
                "/advice", params={"category": "translation", "as_of": T0.isoformat()}
            ).json()
            assert body["verb"] == "recommend"
            assert body["recommend"] == "svc_hi"
            assert body["confidence"] == pytest.approx(0.986, abs=1e-3)
            assert body["margin"] == pytest.approx(0.8709, abs=1e-3)
            assert body["budget_impact"] is None
            assert body["evidence"] == "/decision/explain/svc_hi"
            assert body["category"] == "translation"
            assert body["as_of"] == T0.isoformat()

    def test_keep_when_current_is_top(self, tmp_path: Path) -> None:
        """current 即榜首 → keep；比较对象=次名，reason 不带"较你选的"。"""
        with _client(tmp_path, gateway=_switch_gateway()) as client:
            _switch_fixture(client)
            body = client.get(
                "/advice",
                params={
                    "category": "translation",
                    "current": "svc_hi",
                    "as_of": T0.isoformat(),
                },
            ).json()
            assert body["verb"] == "keep"
            assert body["recommend"] == "svc_hi"
            assert body["margin"] == pytest.approx(0.8709, abs=1e-3)
            assert "较你选的" not in body["reason"]

    def test_keep_single_service_partition(self, tmp_path: Path) -> None:
        """同分区唯一服务且 current 即它 → keep、margin 无次名可比 = null。"""
        gateway = FakeGatewayStatsClient(
            stats={
                "services": [_stats_row("svc_solo", last_activity=T0 - timedelta(minutes=30))],
                "totals": {},
            }
        )
        with _client(tmp_path, gateway=gateway) as client:
            assert (
                client.post(
                    "/manifests", json=_manifest("svc_solo", 137, W_A, category="translation")
                ).status_code
                == 201
            )
            body = client.get(
                "/advice",
                params={
                    "category": "translation",
                    "current": "svc_solo",
                    "as_of": T0.isoformat(),
                },
            ).json()
            assert body["verb"] == "keep"
            assert body["recommend"] == "svc_solo"
            assert body["margin"] is None
            assert body["reason"] == "刚刚活跃"

    def test_indifferent_when_margin_below_deadzone(self, tmp_path: Path) -> None:
        """仅 freshness 差 1 小时 → 分差 ~0.0025 < 0.05 → indifferent（不值得换）。"""
        gateway = FakeGatewayStatsClient(
            stats={
                "services": [
                    _stats_row("svc_ten", distinct=2, last_activity=T0 - timedelta(hours=10)),
                    _stats_row("svc_eleven", distinct=2, last_activity=T0 - timedelta(hours=11)),
                ],
                "totals": {},
            }
        )
        with _client(tmp_path, gateway=gateway) as client:
            for i, sid in enumerate(["svc_ten", "svc_eleven"]):
                assert (
                    client.post(
                        "/manifests",
                        json=_manifest(sid, 137 + i, [W_A, W_B][i], category="translation"),
                    ).status_code
                    == 201
                )
            _seed_charged(client.app.state.store, W_A, 5000)
            _seed_charged(client.app.state.store, W_B, 5000)
            body = client.get(
                "/advice",
                params={
                    "category": "translation",
                    "current": "svc_eleven",
                    "as_of": T0.isoformat(),
                },
            ).json()
            assert body["verb"] == "indifferent"
            assert body["recommend"] == "svc_ten"
            assert 0 <= body["margin"] < 0.05

    def test_current_not_in_partition_treated_as_recommend(self, tmp_path: Path) -> None:
        """current 不在分区内（他类目/已下线/笔误）→ 按纯推荐处理（宽松语义，文档化）。"""
        with _client(tmp_path, gateway=_switch_gateway()) as client:
            _switch_fixture(client)
            body = client.get(
                "/advice",
                params={
                    "category": "translation",
                    "current": "svc_ghost",
                    "as_of": T0.isoformat(),
                },
            ).json()
            assert body["verb"] == "recommend"
            assert "较你选的" not in body["reason"]


class TestAdviceReason:
    def test_switch_reason_three_segments_and_truncation(self, tmp_path: Path) -> None:
        """四条信号全命中 → 截断为前三段（freshness 被截），current 子句另拼在后。"""
        with _client(tmp_path, gateway=_switch_gateway()) as client:
            _switch_fixture(client)
            body = client.get(
                "/advice",
                params={
                    "category": "translation",
                    "current": "svc_lo",
                    "as_of": T0.isoformat(),
                },
            ).json()
            assert body["verb"] == "switch"
            assert body["recommend"] == "svc_hi"
            assert body["margin"] == pytest.approx(0.8709, abs=1e-3)
            assert body["reason"] == (
                "收入明显领先 + 履约 100% vs 50% + 延迟快 3000 ms + 较你选的 svc_lo"
            )
            # 备选 = 榜首之外按分取 2 条；why=同模板最显著一条（收入分量差 1.0）
            assert body["alternatives"] == [{"id": "svc_lo", "why": "收入明显落后"}]

    def test_reason_freshness_segment_included_when_latency_close(self, tmp_path: Path) -> None:
        """p95 差 ≤1000ms 不触发延迟段 → freshness 段入围前三。"""
        gateway = FakeGatewayStatsClient(
            stats={
                "services": [
                    _stats_row(
                        "svc_hi2",
                        success=2,
                        settled=0,
                        aborted=0,
                        p95=1000,
                        distinct=3,
                        last_activity=T0 - timedelta(hours=2),
                    ),
                    _stats_row(
                        "svc_lo2",
                        success=1,
                        settled=0,
                        aborted=1,
                        p95=1500,
                        distinct=1,
                        last_activity=T0 - timedelta(hours=300),
                    ),
                ],
                "totals": {},
            }
        )
        with _client(tmp_path, gateway=gateway) as client:
            assert (
                client.post(
                    "/manifests", json=_manifest("svc_hi2", 137, W_A, category="translation")
                ).status_code
                == 201
            )
            assert (
                client.post(
                    "/manifests", json=_manifest("svc_lo2", 138, W_B, category="translation")
                ).status_code
                == 201
            )
            _seed_charged(client.app.state.store, W_A, 100_000)
            _seed_charged(client.app.state.store, W_B, 1000)
            body = client.get(
                "/advice",
                params={
                    "category": "translation",
                    "current": "svc_lo2",
                    "as_of": T0.isoformat(),
                },
            ).json()
            assert body["reason"] == (
                "收入明显领先 + 履约 100% vs 50% + 2 小时前活跃 + 较你选的 svc_lo2"
            )


class TestAdviceInsufficient:
    def test_empty_partition(self, tmp_path: Path) -> None:
        """分区无 active 服务 → insufficient_data，无备选可给。"""
        with _client(tmp_path) as client:
            assert client.post("/manifests", json=_manifest("svc_t1", 137, W_A)).status_code == 201
            body = client.get(
                "/advice", params={"category": "analysis", "as_of": T0.isoformat()}
            ).json()
            assert body["verb"] == "insufficient_data"
            assert body["recommend"] is None
            assert body["confidence"] is None
            assert body["margin"] is None
            assert body["evidence"] is None
            assert body["reason"] == "分区暂无足够数据，建议按价格与服务描述自选"
            assert body["alternatives"] == []

    def test_all_rows_without_fulfillment(self, tmp_path: Path) -> None:
        """全无履约数据（网关无该分区行）→ insufficient_data + 价格升序前 2 兜底。"""
        with _client(tmp_path) as client:  # 默认 fake 网关：stats 空
            assert (
                client.post(
                    "/manifests",
                    json=_manifest(
                        "svc_pricey",
                        137,
                        W_A,
                        category="translation",
                        amount="0.03",
                        amount_raw="30000",
                    ),
                ).status_code
                == 201
            )
            assert (
                client.post(
                    "/manifests", json=_manifest("svc_cheap", 138, W_B, category="translation")
                ).status_code
                == 201
            )
            body = client.get(
                "/advice",
                params={
                    "category": "translation",
                    "daily_budget_raw": 100000,
                    "as_of": T0.isoformat(),
                },
            ).json()
            assert body["verb"] == "insufficient_data"
            # 无 recommend → budget_impact 也不给（没有可算占比的价格）
            assert body["budget_impact"] is None
            assert body["alternatives"] == [
                {"id": "svc_cheap", "why": "价格 0.01"},
                {"id": "svc_pricey", "why": "价格 0.03"},
            ]


class TestAdviceBudgetAndEvidence:
    def test_budget_impact_share_of_daily(self, tmp_path: Path) -> None:
        """daily_budget_raw=200000、榜首价 10000 → "0.01 / 日额 0.05"（占比=price/budget）。"""
        with _client(tmp_path, gateway=_switch_gateway()) as client:
            _switch_fixture(client)
            body = client.get(
                "/advice",
                params={
                    "category": "translation",
                    "daily_budget_raw": 200000,
                    "as_of": T0.isoformat(),
                },
            ).json()
            assert body["budget_impact"] == "0.01 / 日额 0.05"

    def test_budget_raw_must_be_positive(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            assert client.get("/advice", params={"daily_budget_raw": 0}).status_code == 422

    def test_evidence_pointer_shape(self, tmp_path: Path) -> None:
        """evidence 恒指向 recommend 的 explain 二级指针（非证据本体）。"""
        with _client(tmp_path, gateway=_switch_gateway()) as client:
            _switch_fixture(client)
            body = client.get(
                "/advice", params={"category": "translation", "as_of": T0.isoformat()}
            ).json()
            assert body["evidence"] == f"/decision/explain/{body['recommend']}"
            # 指针可解引用（同服务真实存在 explain 端点）
            assert client.get(body["evidence"]).status_code == 200

    def test_unknown_category_422(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            resp = client.get("/advice", params={"category": "nonsense"})
            assert resp.status_code == 422
            assert resp.json()["code"] == "unknown_category"


class TestAdviceSameEngine:
    def test_recommend_equals_decision_services_top(self, tmp_path: Path) -> None:
        """同引擎一致性：advice 判定 == /decision/services 同分区排序可复算。"""
        gateway = FakeGatewayStatsClient(
            stats={
                "services": [
                    _stats_row(
                        "svc_top",
                        success=2,
                        settled=0,
                        aborted=0,
                        p95=1000,
                        distinct=3,
                        last_activity=T0 - timedelta(hours=1),
                    ),
                    _stats_row(
                        "svc_mid",
                        success=3,
                        settled=3,
                        aborted=1,
                        p95=2000,
                        distinct=2,
                        last_activity=T0 - timedelta(hours=5),
                    ),
                    _stats_row(
                        "svc_low",
                        success=1,
                        settled=0,
                        aborted=1,
                        p95=4000,
                        distinct=1,
                        last_activity=T0 - timedelta(hours=300),
                    ),
                ],
                "totals": {},
            }
        )
        with _client(tmp_path, gateway=gateway) as client:
            for i, (sid, wallet, revenue) in enumerate(
                [("svc_top", W_A, 100_000), ("svc_mid", W_B, 10_000), ("svc_low", W_C, 1000)]
            ):
                assert (
                    client.post(
                        "/manifests", json=_manifest(sid, 137 + i, wallet, category="translation")
                    ).status_code
                    == 201
                )
                _seed_charged(client.app.state.store, wallet, revenue)
            params = {"category": "translation", "as_of": T0.isoformat()}
            advice = client.get("/advice", params=params).json()
            services = client.get("/decision/services", params=params).json()["services"]
            assert advice["recommend"] == services[0]["service_id"]
            assert advice["confidence"] == services[0]["score"]
            assert advice["margin"] == round(services[0]["score"] - services[1]["score"], 4)
            assert [a["id"] for a in advice["alternatives"]] == [
                s["service_id"] for s in services[1:3]
            ]
