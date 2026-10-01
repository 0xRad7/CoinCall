"""unit：FastAPI 端点测试（M1/M2/M3/M4/M8），链层全 mock（dependency_overrides + respx）。

对应 02 篇 test_10 矩阵：路由/校验/错误映射/dry_run 语义全覆盖。
"""

from typing import ClassVar

import httpx
import pytest
import respx
from eth_abi import encode as abi_encode
from eth_utils import function_abi_to_4byte_selector
from fastapi.testclient import TestClient
from httpx import Response

from app.core.deps import (
    get_request_explorer,
    get_request_keystore,
    get_request_tx,
    get_request_web3,
)
from app.core.explorer import ExplorerClient
from app.main import create_app
from tests.fakes import (
    ACCOUNT_A,
    ACCOUNT_B,
    CHAIN_ID,
    TX_HASH,
    USDT,
    make_fake_keystore,
    make_fake_tx_service,
    make_fake_w3,
)

pytestmark = pytest.mark.unit

API = "/api/v1"


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    fake_w3 = make_fake_w3()
    app.dependency_overrides[get_request_web3] = lambda: fake_w3
    app.dependency_overrides[get_request_explorer] = lambda: ExplorerClient(
        make_fake_explorer_client()
    )
    app.dependency_overrides[get_request_keystore] = make_fake_keystore
    app.dependency_overrides[get_request_tx] = make_fake_tx_service
    with TestClient(app) as tc:
        yield tc


def make_fake_explorer_client() -> httpx.Client:
    """Blockscout 桩：不触网，由 respx 拦截具体路径。"""
    return httpx.Client(base_url="https://scan.bohr.life/api/v2")


@respx.mock
class TestM1ChainInfo:
    def test_chain_info(self, client: TestClient) -> None:
        r = client.get(f"{API}/chain/info")
        assert r.status_code == 200
        body = r.json()
        assert body["chain_id"] == CHAIN_ID
        assert body["network"] == "testnet"
        assert "Geth" in body["client_version"]
        assert body["block_number"] > 0

    def test_chain_gas_constant(self, client: TestClient) -> None:
        body = client.get(f"{API}/chain/gas").json()
        assert body["gas_price_wei"] == 20 * 10**9
        assert body["base_fee_per_gas_wei"] == 0
        assert body["transfer_cost_wei"] == 21000 * 20 * 10**9

    @respx.mock
    def test_chain_stats(self, client: TestClient) -> None:
        respx.get("https://scan.bohr.life/api/v2/stats").mock(
            return_value=Response(
                200,
                json={
                    "total_transactions": "980990",
                    "total_addresses": "14086",
                    "transactions_today": "3021",
                    "coin_price": "10.34912",
                    "average_block_time": "662.0",
                },
            )
        )
        body = client.get(f"{API}/chain/stats").json()
        assert body["total_transactions"] == 980990
        assert body["transactions_today"] == 3021
        assert body["coin_price_usd"] == 10.34912

    def test_block_detail(self, client: TestClient) -> None:
        body = client.get(f"{API}/chain/blocks/123").json()
        assert body["number"] == 123
        assert "tx_hashes" in body

    def test_chain_health_all_green(self, client: TestClient) -> None:
        respx.post("https://bundler.bohr.life/rpc/").mock(
            return_value=Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x3c8"})
        )
        respx.get("https://scan.bohr.life/api/v2/stats").mock(
            return_value=Response(200, json={"total_addresses": "1"})
        )
        body = client.get(f"{API}/chain/health").json()
        assert body["ok"] is True
        for channel in ("rpc", "bundler", "explorer"):
            assert body["channels"][channel]["ok"] is True


class TestM2Accounts:
    def test_create_account_hides_key_by_default(self, client: TestClient) -> None:
        r = client.post(f"{API}/accounts", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["address"]
        assert "private_key" not in body

    def test_create_account_reveal_once(self, client: TestClient) -> None:
        body = client.post(f"{API}/accounts", json={"reveal_private_key": True}).json()
        assert body["private_key"].startswith("0x")

    def test_balances_view(self, client: TestClient) -> None:
        r = client.get(f"{API}/accounts/{ACCOUNT_A}/balances")
        assert r.status_code == 200
        body = r.json()
        assert body["native"]["symbol"] == "BOT"
        assert body["native"]["balance_wei"] == "1000000000000000000"
        assert isinstance(body["tokens"], list)

    def test_nonce_view(self, client: TestClient) -> None:
        body = client.get(f"{API}/accounts/{ACCOUNT_A}/nonce").json()
        assert body["latest"] == 0 and body["pending"] == 0

    def test_bad_address_rejected(self, client: TestClient) -> None:
        r = client.get(f"{API}/accounts/notanaddress/nonce")
        assert r.status_code == 422
        assert r.json()["error"] == "service_error"


class TestM3Tx:
    def test_transfer_defaults_to_dry_run(self, client: TestClient) -> None:
        r = client.post(
            f"{API}/tx/transfer",
            json={"from_address": ACCOUNT_A, "to_address": ACCOUNT_B, "value_bot": "0.01"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["dry_run"] is True
        assert "unsigned_tx" in body

    def test_transfer_missing_fields_422(self, client: TestClient) -> None:
        r = client.post(f"{API}/tx/transfer", json={"from_address": ACCOUNT_A})
        assert r.status_code == 422

    def test_send_raw(self, client: TestClient) -> None:
        r = client.post(f"{API}/tx/send-raw", json={"raw_tx": "0x" + "02" * 100})
        assert r.status_code == 200
        assert r.json()["tx_hash"].startswith("0x")

    def test_tx_status(self, client: TestClient) -> None:
        body = client.get(f"{API}/tx/{TX_HASH}").json()
        assert body["found"] is True
        assert body["status"] == 1

    def test_tx_events(self, client: TestClient) -> None:
        body = client.get(f"{API}/tx/{TX_HASH}/events").json()
        assert "events" in body


class TestM4Tokens:
    def test_token_list(self, client: TestClient) -> None:
        body = client.get(f"{API}/tokens").json()
        symbols = {t["symbol"] for t in body["tokens"]}
        assert {"WBOT", "USDT"} <= symbols

    def test_token_info(self, client: TestClient) -> None:
        body = client.get(f"{API}/tokens/{USDT}/info").json()
        assert body["address"] == USDT
        assert "symbol" in body

    def test_erc20_transfer_dry_run_default(self, client: TestClient) -> None:
        r = client.post(
            f"{API}/tokens/erc20/transfer",
            json={
                "token": USDT,
                "from_address": ACCOUNT_A,
                "to_address": ACCOUNT_B,
                "amount": "1.5",
            },
        )
        assert r.status_code == 200
        assert r.json()["dry_run"] is True

    def test_erc20_allowance_query(self, client: TestClient) -> None:
        r = client.post(
            f"{API}/tokens/erc20/allowance",
            json={"token": USDT, "owner": ACCOUNT_A, "spender": ACCOUNT_B},
        )
        assert r.status_code == 200
        assert "allowance_raw" in r.json()

    def test_erc721_owner_and_uri(self, client: TestClient) -> None:
        r1 = client.get(f"{API}/tokens/erc721/{USDT}/owner/1")
        assert r1.status_code == 200
        r2 = client.get(f"{API}/tokens/erc721/{USDT}/token/1/uri")
        assert r2.status_code == 200


class TestM8Contracts:
    ABI: ClassVar = [
        {
            "name": "balanceOf",
            "type": "function",
            "inputs": [{"name": "a", "type": "address"}],
            "outputs": [{"type": "uint256"}],
        }
    ]

    def test_contract_call(self, client: TestClient) -> None:
        r = client.post(
            f"{API}/contracts/call",
            json={"to": USDT, "abi": self.ABI, "method": "balanceOf", "args": [ACCOUNT_A]},
        )
        assert r.status_code == 200
        assert "result" in r.json()

    def test_contract_call_unknown_method_422(self, client: TestClient) -> None:
        r = client.post(
            f"{API}/contracts/call",
            json={"to": USDT, "abi": self.ABI, "method": "nope", "args": []},
        )
        assert r.status_code == 422

    def test_contract_send_dry_run_default(self, client: TestClient) -> None:
        r = client.post(
            f"{API}/contracts/send",
            json={
                "from_address": ACCOUNT_A,
                "to": USDT,
                "abi": self.ABI,
                "method": "balanceOf",
                "args": [ACCOUNT_A],
            },
        )
        assert r.status_code == 200
        assert r.json()["dry_run"] is True

    def test_contract_deploy_dry_run(self, client: TestClient) -> None:
        r = client.post(
            f"{API}/contracts/deploy",
            json={"from_address": ACCOUNT_A, "bytecode": "0x600a"},
        )
        assert r.status_code == 200
        assert r.json()["dry_run"] is True

    def test_decode_calldata(self, client: TestClient) -> None:
        selector = function_abi_to_4byte_selector(self.ABI[0]).hex()
        payload = abi_encode(["address"], [ACCOUNT_A]).hex()
        r = client.post(
            f"{API}/contracts/decode",
            json={"abi": self.ABI, "calldata": f"0x{selector}{payload}"},
        )
        assert r.status_code == 200
        decoded = r.json()["decoded"]
        assert decoded["function"] == "balanceOf(address)"
        assert decoded["args"]["a"].lower() == ACCOUNT_A.lower()
