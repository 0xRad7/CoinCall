"""live：Indexer 数据面（02 篇 test_08 矩阵）：Blockscout v2 分页 + getLogs 窗口 + 真实入库。"""

import pytest

from app.core.abis.erc20 import TRANSFER_TOPIC
from app.core.chains import get_chain
from app.modules.indexer import IndexerService
from app.storage.duckdb import DuckStore

pytestmark = pytest.mark.live

CHAIN = get_chain("testnet")
USDT = CHAIN.contracts.usdt
LOGS_WINDOW = 200  # live 冒烟用小窗口（5000 窗口能力由 01_connectivity 实测背书）


class TestExplorerDataPlane:
    def test_blocks_paging(self, explorer) -> None:
        page = explorer.get("/blocks")
        assert isinstance(page.get("items"), list) and page["items"]

    def test_transactions_paging_params(self, explorer) -> None:
        page = explorer.get("/transactions", params={"filter_to": USDT})
        assert "items" in page

    def test_token_holders_non_empty(self, explorer) -> None:
        page = explorer.get(f"/tokens/{USDT}/holders")
        assert page.get("items"), "USDT holders 不应为空"


class TestGetLogs:
    def test_usdt_transfer_logs_window(self, w3) -> None:
        tip = w3.eth.block_number
        logs = w3.eth.get_logs(
            {
                "address": USDT,
                "fromBlock": max(0, tip - LOGS_WINDOW),
                "toBlock": "latest",
                "topics": [TRANSFER_TOPIC],
            }
        )
        assert isinstance(logs, list)  # 窗口可用即通过（近空链可能为空）
        for log in logs[:5]:
            assert log["topics"][0].hex() == TRANSFER_TOPIC


class TestIndexerSyncLive:
    """真实链小窗口 → 临时 DuckDB 入库冒烟（服务 /indexer/sync 的内核路径）。"""

    def test_sync_blocks_small_window(self, w3, tmp_path) -> None:
        store = DuckStore(tmp_path / "live.duckdb", network="testnet")
        indexer = IndexerService(w3=w3, store=store, chain=CHAIN)
        result = indexer.sync_blocks(window=3)
        assert result["synced_blocks"] == 3
        assert store.query("SELECT count(*) c FROM blocks")[0][0] == 3
        store.close()

    def test_sync_logs_watermark_advances(self, w3, tmp_path) -> None:
        store = DuckStore(tmp_path / "live2.duckdb", network="testnet")
        indexer = IndexerService(w3=w3, store=store, chain=CHAIN)
        first = indexer.sync_logs(window=5)
        second = indexer.sync_logs(window=5)
        assert second["to_block"] > first["from_block"]
        assert store.get_watermark("logs") == second["to_block"]
        store.close()
