"""unit+live：GET /contracts/logs 只读 eth_getLogs 透传（窗口 ≤5000 / limit 上限 / 原始 log 形态）。

发起人指示（P1-2）：链上功能优先走 bot-chain-api，缺端点先补端点；
本端点只透传原始 log（ABI 解码由调用方自解，如 core 的 Charged 索引）。
混合 marker 文件不设模块级 pytestmark（gateway C-03 纪律，逐用例打标）。
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from web3 import Web3
from web3.exceptions import Web3RPCError

from app.core.config import get_settings
from app.core.deps import get_request_web3
from app.main import create_app
from app.modules.contracts import contract_logs
from tests.fakes import ACCOUNT_A, BLOCK_NUMBER, make_fake_w3

API = "/api/v1/contracts/logs"

#: PayVault(0xFe91…) Charged(address,address,uint256,bytes32) 事件签名哈希
CHARGED_TOPIC0 = "0x" + "7cbb811de7ebfc8f2d6195f3af05b0b12fc3e9c8b48a2dbf3ff1c4ef81291dbf"
#: PayVault 部署块（eth_getCode 实证：25795947 无代码 / 25795948 起 6696B）
PAYVAULT = "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471"
PAYVAULT_DEPLOY_BLOCK = 25_795_948
#: 链上已知 Charged provider（keeper 历史结算）
KNOWN_PROVIDER = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


def make_fake_log(
    *, block_number: int = BLOCK_NUMBER, log_index: int = 0, address: str = ACCOUNT_A
) -> dict[str, Any]:
    """web3 get_logs 返回形态的桩（AttributeDict 兼容 dict 取值）。"""
    return {
        "address": address,
        "topics": [
            Web3.to_bytes(hexstr=CHARGED_TOPIC0),
            b"\x00" * 12 + bytes.fromhex(KNOWN_PROVIDER[2:]),
        ],
        "data": (10000).to_bytes(32, "big"),
        "blockNumber": block_number,
        "blockHash": Web3.to_bytes(hexstr="0x" + "bb" * 32),
        "transactionHash": Web3.to_bytes(hexstr="0x" + "cc" * 32),
        "transactionIndex": 0,
        "logIndex": log_index,
        "removed": False,
    }


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """零网络应用实例：web3 全 mock + DuckDB 隔离到 tmp_path（C-14：服务进程持库时不受牵连）。"""
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "unit.duckdb"))
    get_settings.cache_clear()
    app = create_app()
    fake_w3 = make_fake_w3()
    app.dependency_overrides[get_request_web3] = lambda: fake_w3
    with TestClient(app) as tc:
        tc.fake_w3 = fake_w3  # type: ignore[attr-defined]  # 测试侧桩句柄（app.state.w3 是真实实例）
        yield tc
    get_settings.cache_clear()


class TestContractLogsUnit:
    @pytest.mark.unit
    def test_logs_passthrough_raw_shape(self, client: TestClient) -> None:
        client.fake_w3.eth.get_logs.return_value = [
            make_fake_log(log_index=0, address=PAYVAULT),
            make_fake_log(log_index=1, address=PAYVAULT),
        ]
        resp = client.get(
            f"{API}",
            params={
                "address": PAYVAULT,
                "from_block": BLOCK_NUMBER,
                "to_block": BLOCK_NUMBER + 10,
                "topic0": CHARGED_TOPIC0,
                "limit": 100,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 2
        assert body["truncated"] is False
        log = body["logs"][0]
        assert log["address"] == PAYVAULT
        assert log["topics"][0] == CHARGED_TOPIC0  # 0x 前缀归一（POA HexBytes 无前缀陷阱）
        assert log["topics"][1][-40:] == KNOWN_PROVIDER[2:].lower()
        assert log["data"].startswith("0x")
        assert isinstance(log["block_number"], int)
        assert isinstance(log["transaction_hash"], str)
        assert isinstance(log["log_index"], int)
        # getLogs 参数透传（address 规范化 + topic0 过滤）
        criteria = client.fake_w3.eth.get_logs.call_args[0][0]
        assert criteria["fromBlock"] == BLOCK_NUMBER
        assert criteria["toBlock"] == BLOCK_NUMBER + 10
        assert criteria["address"] == PAYVAULT
        assert criteria["topics"] == [CHARGED_TOPIC0]

    @pytest.mark.unit
    def test_logs_without_address_and_topic(self, client: TestClient) -> None:
        client.fake_w3.eth.get_logs.return_value = []
        resp = client.get(f"{API}", params={"from_block": 1, "to_block": 2})
        assert resp.status_code == 200
        assert resp.json()["count"] == 0
        criteria = client.fake_w3.eth.get_logs.call_args[0][0]
        assert "address" not in criteria
        assert "topics" not in criteria

    @pytest.mark.unit
    def test_logs_window_over_5000_rejected(self, client: TestClient) -> None:
        resp = client.get(f"{API}", params={"from_block": 0, "to_block": 5000})
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"] == "service_error"
        assert body["code"] == "window_too_large"

    @pytest.mark.unit
    def test_logs_from_greater_than_to_rejected(self, client: TestClient) -> None:
        resp = client.get(f"{API}", params={"from_block": 10, "to_block": 9})
        assert resp.status_code == 422
        assert resp.json()["code"] == "bad_range"

    @pytest.mark.unit
    def test_logs_bad_address_rejected(self, client: TestClient) -> None:
        resp = client.get(f"{API}", params={"address": "0x123", "from_block": 1, "to_block": 2})
        assert resp.status_code == 422
        assert resp.json()["code"] == "bad_address"

    @pytest.mark.unit
    def test_logs_bad_topic0_rejected(self, client: TestClient) -> None:
        resp = client.get(
            f"{API}",
            params={"from_block": 1, "to_block": 2, "topic0": "0xdeadbeef"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "bad_topic"

    @pytest.mark.unit
    def test_logs_limit_truncates(self, client: TestClient) -> None:
        client.fake_w3.eth.get_logs.return_value = [make_fake_log(log_index=i) for i in range(3)]
        resp = client.get(f"{API}", params={"from_block": 1, "to_block": 2, "limit": 2})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert body["truncated"] is True

    @pytest.mark.unit
    def test_logs_limit_over_max_rejected(self, client: TestClient) -> None:
        resp = client.get(f"{API}", params={"from_block": 1, "to_block": 2, "limit": 5001})
        assert resp.status_code == 422  # pydantic Query 校验

    @pytest.mark.unit
    def test_logs_chain_error_maps_502(self, client: TestClient) -> None:
        client.fake_w3.eth.get_logs.side_effect = Web3RPCError("boom")
        resp = client.get(f"{API}", params={"from_block": 1, "to_block": 2})
        assert resp.status_code == 502
        assert resp.json()["error"] == "chain_error"


class TestContractLogsLive:
    """真链只读：经路由函数直调（真实 w3，无 TestClient/DuckDB 副作用）。"""

    @pytest.mark.live
    def test_logs_payvault_deploy_window_real(self, w3) -> None:
        view = contract_logs(
            w3=w3,
            address=PAYVAULT,
            from_block=PAYVAULT_DEPLOY_BLOCK,
            to_block=PAYVAULT_DEPLOY_BLOCK + 4999,
            topic0=CHARGED_TOPIC0,
            limit=5000,
        )
        assert view.count >= 1, "PayVault 部署后 5000 块窗口内应有历史 Charged（实测 8 笔）"
        assert all(log["topics"][0] == CHARGED_TOPIC0 for log in view.logs)
        providers = {log["topics"][1][-40:].lower() for log in view.logs}
        assert KNOWN_PROVIDER[2:].lower() in providers
        assert any(int(log["data"], 16) > 0 for log in view.logs), "Charged value 恒为正"
