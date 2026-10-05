"""T22/T23：06 篇排行榜——收入优先排序/空库优雅/proof 哈希清单 + GMV==链上 Charged 总额。

双源口径：收入=链上 Charged（经 8010 /contracts/logs 增量拉取，水位表记档）；
活跃度=网关 /internal/stats/calls；排序恒收入优先（00 铁律：收入=最硬信誉）。
混合 marker 文件不设模块级 pytestmark（逐用例打标）。
"""

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.leaderboard import CHARGED_TOPIC0, BotChainClient
from tests.conftest import (
    VALID_MANIFEST,
    FakeChainClient,
    FakeGatewayStatsClient,
    FakeIdentityClient,
    make_charged_log,
)

#: 链上已知事实（2026-10-01 实测）：PayVault 部署块 25795948；
#: 历史 Charged 8 笔共 10_070_000（provider 0xc37f…1 笔 10_000_000 + 0x3c44…7 笔）
PAYVAULT_DEPLOY_BLOCK = 25_795_948
KNOWN_ONCHAIN_GMV = 10_070_000

PROVIDER_BIG = "0xc37ffe97b4d2c3d0187b1ddedf273e52a461b63a"  # 单笔大额
PROVIDER_3C44 = "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"  # 多笔小额
PROVIDER_137 = "0x1234567890abcdef1234567890abcdef12345678"  # == VALID_MANIFEST.wallet 小写


def _chain_with_history(tip: int = PAYVAULT_DEPLOY_BLOCK + 100) -> FakeChainClient:
    """可编程链源：复刻链上已知形态（大额 1 笔 + 小额 7 笔 ×10000）。"""
    logs: dict[int, list[dict[str, Any]]] = {
        PAYVAULT_DEPLOY_BLOCK + 19: [make_charged_log(provider=PROVIDER_BIG, value_raw=10_000_000)],
        PAYVAULT_DEPLOY_BLOCK + 31: [
            make_charged_log(
                provider=PROVIDER_3C44, value_raw=10_000, tx_hash="0x" + "01" * 32, log_index=i
            )
            for i in range(3)
        ],
        PAYVAULT_DEPLOY_BLOCK + 42: [
            make_charged_log(
                provider=PROVIDER_3C44, value_raw=10_000, tx_hash="0x" + "02" * 32, log_index=i
            )
            for i in range(3)
        ],
        PAYVAULT_DEPLOY_BLOCK + 99: [
            make_charged_log(provider=PROVIDER_3C44, value_raw=10_000, tx_hash="0x" + "03" * 32)
        ],
    }
    return FakeChainClient(tip=tip, logs_by_block=logs)


def _client(
    tmp_path: Path,
    *,
    chain: FakeChainClient | None = None,
    gateway: FakeGatewayStatsClient | None = None,
    min_interval: float = 0.0,
) -> TestClient:
    settings = Settings(
        duckdb_path=str(tmp_path / "core.duckdb"),
        pay_vault_deploy_block=PAYVAULT_DEPLOY_BLOCK,
        leaderboard_min_sync_interval=min_interval,
    )
    app = create_app(
        settings,
        identity_client=FakeIdentityClient(),
        chain_client=chain or FakeChainClient(),
        gateway_client=gateway or FakeGatewayStatsClient(),
    )
    return TestClient(app)


class TestLeaderboardUnit:
    @pytest.mark.unit
    def test_providers_revenue_first_ordering(self, tmp_path: Path) -> None:
        with _client(tmp_path, chain=_chain_with_history()) as client:
            client.post("/providers", json={"agent_id": 137, "display_name": "Team booth-demo"})
            resp = client.get("/leaderboard/providers")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["order"] == "revenue"
            rows = body["providers"]
            # 收入优先：大额 provider 第一（10_000_000 > 70_000 > 0）
            assert [r["wallet"] for r in rows[:2]] == [PROVIDER_BIG, PROVIDER_3C44]
            assert rows[0]["revenue_raw"] == 10_000_000
            assert rows[1]["revenue_raw"] == 70_000
            assert rows[1]["charged_count"] == 7
            # 零收入注册者垫底但可见（display_name 回填）
            assert rows[-1]["wallet"] == PROVIDER_137
            assert rows[-1]["display_name"] == "Team booth-demo"
            assert rows[-1]["revenue_raw"] == 0
            # 展示值与权威值一致（10 USDT / 0.07 USDT）
            assert rows[0]["revenue"] == "10"
            assert rows[1]["revenue"] == "0.07"

    @pytest.mark.unit
    def test_sync_backfills_from_deploy_block_and_sets_watermark(self, tmp_path: Path) -> None:
        chain = _chain_with_history()
        with _client(tmp_path, chain=chain) as client:
            client.get("/leaderboard/providers")
            # 首跑自部署块-64 回补，分窗拉到 tip
            assert chain.pulled_windows == [(PAYVAULT_DEPLOY_BLOCK - 64, chain.tip)]
            watermark = client.app.state.store.get_watermark("charged_events")
            assert watermark == chain.tip

    @pytest.mark.unit
    def test_incremental_sync_pulls_only_new_window(self, tmp_path: Path) -> None:
        chain = _chain_with_history()
        with _client(tmp_path, chain=chain, min_interval=0.0) as client:
            client.get("/stats/overview")
            first_windows = list(chain.pulled_windows)
            chain.tip += 10  # 链前进 10 块
            chain.logs_by_block[chain.tip] = [
                make_charged_log(provider=PROVIDER_3C44, value_raw=10_000, tx_hash="0x" + "04" * 32)
            ]
            ov = client.get("/stats/overview").json()
            assert chain.pulled_windows[: len(first_windows)] == first_windows
            assert chain.pulled_windows[-1] == (first_windows[-1][1] + 1, chain.tip)
            assert ov["charged_count"] == 9
            assert ov["gmv_raw"] == KNOWN_ONCHAIN_GMV + 10_000

    @pytest.mark.unit
    def test_empty_db_graceful_everywhere(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            assert client.get("/leaderboard/services").json()["services"] == []
            assert client.get("/leaderboard/providers").json()["providers"] == []
            assert client.get("/store").json()["services"] == []
            ov = client.get("/stats/overview").json()
            assert ov["gmv_raw"] == 0
            assert ov["charged_count"] == 0
            assert ov["services_total"] == 0
            assert ov["providers_registered"] == 0
            assert ov["degraded"] == []
            proof = client.get(f"/leaderboard/providers/{PROVIDER_3C44}/proof").json()
            assert proof["count"] == 0
            assert proof["events"] == []

    @pytest.mark.unit
    def test_proof_lists_all_tx_hashes(self, tmp_path: Path) -> None:
        with _client(tmp_path, chain=_chain_with_history()) as client:
            proof = client.get(f"/leaderboard/providers/{PROVIDER_3C44}/proof").json()
            assert proof["count"] == 7
            assert proof["revenue_raw"] == 70_000
            tx_hashes = [ev["tx_hash"] for ev in proof["events"]]
            assert len(set(tx_hashes)) == 3  # 3 笔交易（批内多 log）
            assert all(
                ev["explorer_url"].startswith("https://scan.bohr.life/tx/0x")
                for ev in proof["events"]
            )
            big = client.get(f"/leaderboard/providers/{PROVIDER_BIG}/proof").json()
            assert big["count"] == 1 and big["revenue_raw"] == 10_000_000

    @pytest.mark.unit
    def test_proof_bad_wallet_422(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            assert client.get("/leaderboard/providers/0xdeadbeef/proof").status_code == 422

    @pytest.mark.unit
    def test_services_leaderboard_revenue_order_with_activity(self, tmp_path: Path) -> None:
        gateway = FakeGatewayStatsClient(
            stats={
                "services": [
                    {
                        "service_id": "svc_translate_v1",
                        "calls_success": 5,
                        "calls_aborted": 1,
                        "last_call_at": "2026-10-01 10:00:00",
                    },
                    {
                        "service_id": "svc_weather_v1",
                        "calls_success": 2,
                        "calls_aborted": 0,
                        "last_call_at": "2026-10-01 09:00:00",
                    },
                ],
                "totals": {"calls_success": 7, "calls_aborted": 1},
            }
        )
        with _client(tmp_path, chain=_chain_with_history(), gateway=gateway) as client:
            assert client.post("/manifests", json=VALID_MANIFEST).status_code == 201
            weather = dict(VALID_MANIFEST, service_id="svc_weather_v1", name="天气查询")
            weather["provider"] = dict(weather["provider"], agent_id=138, wallet=PROVIDER_BIG)
            assert client.post("/manifests", json=weather).status_code == 201
            rows = client.get("/leaderboard/services").json()["services"]
            # 收入优先：weather（provider=大额 10_000_000）第一，translate（0）第二
            assert [r["service_id"] for r in rows] == ["svc_weather_v1", "svc_translate_v1"]
            first, second = rows
            assert first["revenue_raw"] == 10_000_000
            assert second["calls_success"] == 5
            assert second["calls_aborted"] == 1
            assert second["fail_rate"] == 0.167  # 1/(5+1) 三位舍入
            assert second["last_call_at"] == "2026-10-01 10:00:00"

    @pytest.mark.unit
    def test_store_only_active_services(self, tmp_path: Path) -> None:
        with _client(tmp_path, chain=_chain_with_history()) as client:
            assert client.post("/manifests", json=VALID_MANIFEST).status_code == 201
            paused = dict(VALID_MANIFEST, service_id="svc_paused_v1", status="paused")
            assert client.post("/manifests", json=paused).status_code == 201
            store_rows = client.get("/store").json()["services"]
            assert [r["service_id"] for r in store_rows] == ["svc_translate_v1"]
            svc_rows = client.get("/leaderboard/services").json()["services"]
            assert {r["service_id"] for r in svc_rows} == {"svc_translate_v1", "svc_paused_v1"}

    @pytest.mark.unit
    def test_overview_counts(self, tmp_path: Path) -> None:
        gateway = FakeGatewayStatsClient(
            stats={"services": [], "totals": {"calls_success": 7, "calls_aborted": 1}}
        )
        with _client(tmp_path, chain=_chain_with_history(), gateway=gateway) as client:
            client.post("/providers", json={"agent_id": 137, "display_name": "t"})
            assert client.post("/manifests", json=VALID_MANIFEST).status_code == 201
            ov = client.get("/stats/overview").json()
            assert ov["gmv_raw"] == KNOWN_ONCHAIN_GMV
            assert ov["gmv"] == "10.07"
            assert ov["charged_count"] == 8
            assert ov["calls_success_total"] == 7
            assert ov["services_total"] == 1
            assert ov["services_active"] == 1
            assert ov["providers_registered"] == 1
            assert ov["providers_with_revenue"] == 2
            assert ov["synced_to_block"] == PAYVAULT_DEPLOY_BLOCK + 100

    @pytest.mark.unit
    def test_gateway_down_degrades_not_fails(self, tmp_path: Path) -> None:
        gateway = FakeGatewayStatsClient(error=RuntimeError("gw down"))
        with _client(tmp_path, chain=_chain_with_history(), gateway=gateway) as client:
            ov = client.get("/stats/overview").json()
            assert ov["gmv_raw"] == KNOWN_ONCHAIN_GMV  # 收入侧不受网关影响
            assert any(d.startswith("gateway_stats_failed") for d in ov["degraded"])
            assert ov["calls_success_total"] == 0  # 网关降级时活跃度按 0 口径
            assert client.get("/leaderboard/services").json()["services"] == []  # 无目录不 500

    @pytest.mark.unit
    def test_chain_down_degrades_and_serves_stock(self, tmp_path: Path) -> None:
        chain = _chain_with_history()
        with _client(tmp_path, chain=chain) as client:
            client.get("/stats/overview")  # 首次同步成功入库存
        chain.fail_tip = True  # 模拟链源故障
        with _client(tmp_path, chain=chain) as client2:
            ov = client2.get("/stats/overview").json()
            assert ov["gmv_raw"] == KNOWN_ONCHAIN_GMV  # 按库存返回
            assert any(d.startswith("charged_sync_failed") for d in ov["degraded"])


class TestGmvMatchesOnchainLive:
    @pytest.mark.live
    def test_gmv_matches_onchain(self, tmp_path: Path) -> None:
        """T23：overview GMV == 链上 Charged 总额（PayVault 事件 value 直和，I4 口径）。

        双侧独立取数：overview 走库存水位；对照侧直连 8010 /contracts/logs
        从部署块-64 全窗拉取到 synced_to_block 为止逐笔求和——区块历史不可变，
        相等即通过；不用任何流水表替代（汇合门铁律）。
        """
        settings = Settings(
            duckdb_path=str(tmp_path / "t23.duckdb"),
            leaderboard_min_sync_interval=0.0,
        )
        with TestClient(create_app(settings)) as client:
            ov = client.get("/stats/overview").json()
            assert ov["degraded"] == [], ov["degraded"]
            synced_to = int(ov["synced_to_block"] or 0)
            assert synced_to >= PAYVAULT_DEPLOY_BLOCK

            direct_http = httpx.Client(timeout=20.0, trust_env=False)
            direct = BotChainClient(
                direct_http,
                settings.bot_chain_api_base_url,
                settings.pay_vault_address,
            )
            total = 0
            count = 0
            cursor = PAYVAULT_DEPLOY_BLOCK - 64
            while cursor <= synced_to:
                window_to = min(cursor + 4999, synced_to)
                for log in direct.charged_logs(cursor, window_to):
                    assert log["topics"][0] == CHARGED_TOPIC0
                    total += int(log["data"], 16)
                    count += 1
                cursor = window_to + 1
            direct_http.close()
            assert ov["gmv_raw"] == total, f"GMV {ov['gmv_raw']} != 链上 Charged 总和 {total}"
            assert ov["charged_count"] == count
            # 链上历史下限（2026-10-01 实测 8 笔 10_070_000，历史不可变只增不减）
            assert ov["gmv_raw"] >= KNOWN_ONCHAIN_GMV
