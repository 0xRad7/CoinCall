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

    @respx.mock
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


class TestM5Aa:
    @respx.mock
    def test_aa_config(self, client: TestClient) -> None:
        respx.post("https://bundler.bohr.life/rpc/").mock(
            return_value=Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": ["0x0000000071727de22e5e9d8baf0edac6f37da032"],
                },
            )
        )
        body = client.get(f"{API}/aa/config").json()
        assert body["entry_point"].lower() == "0x0000000071727de22e5e9d8baf0edac6f37da032"
        assert body["supported_entry_points"]

    def test_aa_predict(self, client: TestClient) -> None:
        body = client.post(f"{API}/aa/account/predict", json={"owner": ACCOUNT_A, "salt": 0}).json()
        assert body["address"] == ACCOUNT_A  # fake factory.getAddress 返回 ACCOUNT_A

    def test_aa_userop_build(self, client: TestClient) -> None:
        r = client.post(
            f"{API}/aa/userop/build",
            json={"owner": ACCOUNT_A, "target": ACCOUNT_B, "value_wei": "0"},
        )
        assert r.status_code == 200
        op = r.json()["user_operation"]
        assert (
            op["sender"] and op["maxFeePerGas"] == "30000000000"
        )  # 离散字段（C-16），1.5× 链价激励

    def test_aa_execute_without_signer_422(self, client: TestClient) -> None:
        """owner 无可用签名者（fake resolve_signer 抛 ServiceError）→ 422 no_signer。"""
        r = client.post(
            f"{API}/aa/execute",
            json={"owner": ACCOUNT_B, "target": ACCOUNT_B, "value_wei": "0", "dry_run": False},
        )
        assert r.status_code == 422
        assert r.json()["error"] == "service_error"

    @respx.mock
    def test_aa_userop_status_not_found(self, client: TestClient) -> None:
        respx.post("https://bundler.bohr.life/rpc/").mock(
            return_value=Response(200, json={"jsonrpc": "2.0", "id": 1, "result": None})
        )
        body = client.get(f"{API}/aa/userop/0x{'ab' * 32}").json()
        assert body["found"] is False


class TestM6AgentIdentity:
    def test_contracts_view(self, client: TestClient) -> None:
        body = client.get(f"{API}/agent-identity/contracts").json()
        assert body["identity_registry"]["address"].startswith("0x")

    def test_identity_aggregate_view(self, client: TestClient) -> None:
        body = client.get(f"{API}/agent-identity/5").json()
        assert body["token_id"] == 5
        assert body["owner"] == ACCOUNT_A

    def test_register_dry_run_default(self, client: TestClient) -> None:
        r = client.post(
            f"{API}/agent-identity/register",
            json={"owner": ACCOUNT_A, "agent_uri": "https://x/y"},
        )
        assert r.status_code == 200
        assert r.json()["dry_run"] is True

    @respx.mock
    def test_registry_page(self, client: TestClient) -> None:
        respx.get(
            "https://scan.bohr.life/api/v2/tokens/0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0/transfers"
        ).mock(
            return_value=Response(
                200,
                json={
                    "items": [{"total": {"value": "3"}, "tx_hash": "0x1"}],
                    "next_page_params": None,
                },
            )
        )
        body = client.get(f"{API}/agent-identity/registry").json()
        assert body["items"][0]["token_id"] == "3"

    def test_reputation_and_validations(self, client: TestClient) -> None:
        assert client.get(f"{API}/agent-identity/5/reputation").status_code == 200
        assert client.get(f"{API}/agent-identity/5/validations").status_code == 200


class TestM7Bdex:
    def test_config(self, client: TestClient) -> None:
        body = client.get(f"{API}/bdex/config").json()
        assert body["v2_router"].startswith("0x")

    def test_pairs(self, client: TestClient) -> None:
        body = client.get(f"{API}/bdex/pairs").json()
        assert body[0]["token0"] == USDT

    def test_quote(self, client: TestClient) -> None:
        body = client.post(
            f"{API}/bdex/quote",
            json={
                "token_in": USDT,
                "token_out": "0xD5452816194a3784dBa983426cCe7c122F4abd30",
                "amount_in": "1",
            },
        ).json()
        assert body["amount_in_raw"] == "1000000"
        assert body["amount_out_raw"] == "500000000000000000"

    def test_swap_build_and_dry_run(self, client: TestClient) -> None:
        payload = {
            "from_address": ACCOUNT_A,
            "token_in": USDT,
            "token_out": "0xD5452816194a3784dBa983426cCe7c122F4abd30",
            "amount_in": "1",
        }
        assert client.post(f"{API}/bdex/swap/build", json=payload).status_code == 200
        r = client.post(f"{API}/bdex/swap/execute", json=payload)
        assert r.status_code == 200
        assert r.json()["dry_run"] is True

    def test_pool_volume(self, client: TestClient) -> None:
        body = client.get(f"{API}/bdex/pool/{ACCOUNT_B}/volume").json()
        assert body["swap_count"] == 0


class TestM10Faucet:
    @respx.mock
    def test_status(self, client: TestClient) -> None:
        respx.get("https://api-faucet.bohr.life/botchain/api/v1/faucet/info").mock(
            return_value=Response(
                200,
                json={
                    "code": 0,
                    "message": "success",
                    "data": {
                        "chain_name": "BOT Chain Testnet",
                        "assets": [
                            {
                                "token_symbol": "BOT",
                                "token_type": "native",
                                "claim_amount": "10",
                                "cooldown_hours": 0.1667,
                                "enabled": True,
                                "token_address": "",
                            },
                        ],
                    },
                },
            )
        )
        body = client.get(f"{API}/faucet/status").json()
        assert body["available"] is True
        assert body["automatable"] is False
        assert body["assets"][0]["token_symbol"] == "BOT"

    def test_claim_without_token_returns_manual_hint(self, client: TestClient) -> None:
        body = client.post(
            f"{API}/faucet/claim", json={"address": ACCOUNT_A, "dry_run": False}
        ).json()
        assert body["claimed"] is False
        assert body["manual_url"].startswith("https://faucet.bohr.life")


class TestM9Indexer:
    def test_indexer_status(self, client: TestClient) -> None:
        body = client.get(f"{API}/indexer/status").json()
        assert body["network"] == "testnet"
        assert body["counts"]["blocks"] == 0  # lifespan 建的空库

    def test_sync_logs_smoke_with_fake_w3(self, client: TestClient) -> None:
        r = client.post(f"{API}/indexer/sync/logs", json={"window": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["stream"] == "logs"
        assert body["synced_blocks"] >= 1  # 空 watermark 时 =window；有水位时含 64 块 reorg 回扫
