"""core 单元测试（unit，全离线：构造对象与 TestClient，无任何网络调用）。

覆盖：chains 地址单一来源 / config 环境与主网双重锁 / rpc 客户端装配 /
errors 三段错误模型映射 / log 脱敏与 trace_id / main 应用骨架。
"""

import httpx
import pytest
import respx
from eth_account import Account
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from web3 import Web3

from app.core.chains import CHAINS, get_chain
from app.core.config import Settings
from app.core.errors import (
    ChainError,
    ErrorResponse,
    ServiceError,
    TxRevertedError,
    install_error_handlers,
)
from app.core.explorer import ExplorerClient
from app.core.keystore import Keystore
from app.core.log import REDACTED, TraceIdMiddleware, current_trace_id, redact
from app.core.rpc import make_http_client, make_web3, resolve_proxy
from app.main import create_app
from tests.fakes import ACCOUNT_A, ACCOUNT_B, USDT

pytestmark = pytest.mark.unit

KS_MATERIAL_RIGHT = "ks-material-right"  # 测试口令（非真实秘密）
KS_MATERIAL_WRONG = "ks-material-wrong"  # 测试口令（非真实秘密）
EXPLORER_BASE = "https://scan.bohr.life/api/v2"

TESTNET = "testnet"
MAINNET = "mainnet"
CHAIN_ID_TESTNET = 968
CHAIN_ID_MAINNET = 677
ENTRY_POINT_V07 = "0x0000000071727De22E5E9d8BAf0edAc6f37da032"
GAS_PRICE_GWEI = 20
FAKE_KEY = "0x" + "11" * 32


class TestChains:
    def test_two_networks_defined(self) -> None:
        assert set(CHAINS) == {TESTNET, MAINNET}

    def test_chain_ids(self) -> None:
        assert CHAINS[TESTNET].chain_id == CHAIN_ID_TESTNET
        assert CHAINS[MAINNET].chain_id == CHAIN_ID_MAINNET

    def test_entry_point_shared_singleton(self) -> None:
        for spec in CHAINS.values():
            assert spec.contracts.entry_point == ENTRY_POINT_V07

    def test_all_addresses_checksummed(self) -> None:
        for name, spec in CHAINS.items():
            for field, addr in spec.contracts.model_dump().items():
                assert Web3.is_address(addr), f"{name}.{field} 非法地址: {addr}"
                assert addr == Web3.to_checksum_address(addr), f"{name}.{field} 非 checksum: {addr}"

    def test_erc8004_registry_differs_between_networks(self) -> None:
        assert (
            CHAINS[TESTNET].contracts.identity_registry
            != CHAINS[MAINNET].contracts.identity_registry
        )

    def test_usdt_differs_between_networks(self) -> None:
        assert CHAINS[TESTNET].contracts.usdt != CHAINS[MAINNET].contracts.usdt

    def test_endpoint_domains(self) -> None:
        assert CHAINS[TESTNET].rpc_url.startswith("https://rpc.bohr.life")
        assert CHAINS[MAINNET].rpc_url.startswith("https://rpc.botchain.ai")
        assert "/api/v2" in CHAINS[TESTNET].explorer_api

    def test_get_chain_unknown_network_raises(self) -> None:
        with pytest.raises(KeyError):
            get_chain("devnet")


class TestConfig:
    def test_default_is_testnet(self) -> None:
        spec = Settings(_env_file=None).chain_spec
        assert spec.chain_id == CHAIN_ID_TESTNET

    def test_mainnet_locked_without_explicit_allow(self) -> None:
        s = Settings(_env_file=None, bot_chain_network=MAINNET)
        with pytest.raises(ServiceError):
            _ = s.chain_spec

    def test_mainnet_unlocked_with_allow_flag(self) -> None:
        s = Settings(_env_file=None, bot_chain_network=MAINNET, bot_chain_allow_mainnet=True)
        assert s.chain_spec.chain_id == CHAIN_ID_MAINNET

    def test_funded_key_available_on_testnet(self) -> None:
        s = Settings(_env_file=None, bot_chain_test_private_key=FAKE_KEY)
        assert s.funded_key == FAKE_KEY

    def test_funded_key_refused_on_mainnet(self) -> None:
        s = Settings(
            _env_file=None,
            bot_chain_network=MAINNET,
            bot_chain_allow_mainnet=True,
            bot_chain_test_private_key=FAKE_KEY,
        )
        assert s.funded_key is None

    def test_private_key_never_in_repr(self) -> None:
        s = Settings(_env_file=None, bot_chain_test_private_key=FAKE_KEY)
        assert "1111" not in repr(s)
        assert "1111" not in str(s.model_dump())


class TestRpc:
    def test_http_client_carries_ua_header(self) -> None:
        client = make_http_client(EXPLORER_BASE)
        try:
            assert client.headers["User-Agent"].startswith("Mozilla/5.0")
        finally:
            client.close()

    def test_resolve_proxy_bohr_life_direct(self) -> None:
        assert resolve_proxy("https://rpc.bohr.life/", "http://127.0.0.1:7890") is None

    def test_resolve_proxy_botchain_ai_uses_proxy(self) -> None:
        assert (
            resolve_proxy("https://rpc.botchain.ai/", "http://127.0.0.1:7890")
            == "http://127.0.0.1:7890"
        )

    def test_resolve_proxy_without_config_is_none(self) -> None:
        assert resolve_proxy("https://rpc.botchain.ai/", None) is None

    def test_make_web3_wires_endpoint(self) -> None:
        spec = get_chain(TESTNET)
        w3 = make_web3(spec)
        assert w3.provider.endpoint_uri == spec.rpc_url  # 构造期不触网


class TestErrors:
    @pytest.mark.parametrize(
        ("exc", "expected_status"),
        [
            (ChainError("rpc down"), 502),
            (TxRevertedError("execution reverted: ERC20: insufficient allowance"), 409),
            (ServiceError("no signer for address"), 422),
        ],
    )
    def test_kind_to_http_status(self, exc: Exception, expected_status: int) -> None:
        assert exc.http_status == expected_status

    def test_error_response_model_roundtrip(self) -> None:
        body = ErrorResponse(error="tx_reverted", detail="revert", tx_hash="0xabc").model_dump()
        assert body["error"] == "tx_reverted"
        assert body["trace_id"] is None

    def test_handlers_shape_and_no_stacktrace(self) -> None:
        app = FastAPI()
        install_error_handlers(app)

        @app.get("/boom")
        def boom() -> dict:
            raise ChainError("rpc unreachable")

        @app.get("/revert")
        def revert() -> dict:
            raise TxRevertedError("execution reverted", tx_hash="0xabc")

        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/boom")
        assert r.status_code == 502
        body = r.json()
        assert body["error"] == "chain_error"
        assert body["detail"] == "rpc unreachable"
        assert "Traceback" not in r.text

        r2 = client.get("/revert")
        assert r2.status_code == 409
        assert r2.json()["tx_hash"] == "0xabc"


class TestLog:
    def test_redact_masks_sensitive_keys_recursively(self) -> None:
        payload = {
            "private_key": "0xdeadbeef",
            "nested": {"Authorization": "Bearer tok", "api_key": "k1"},
            "list": [{"X-API-Key": "k2"}],
            "safe": 1,
        }
        out = redact(payload)
        assert out["private_key"] == REDACTED
        assert out["nested"]["Authorization"] == REDACTED
        assert out["nested"]["api_key"] == REDACTED
        assert out["list"][0]["X-API-Key"] == REDACTED
        assert out["safe"] == 1
        assert "deadbeef" not in str(out)

    def test_trace_id_passthrough_and_generate(self) -> None:
        app = FastAPI()
        app.add_middleware(TraceIdMiddleware)

        @app.get("/t")
        def t() -> dict:
            return {"trace_id": current_trace_id()}

        client = TestClient(app)
        r = client.get("/t", headers={"X-Trace-Id": "abc123"})
        assert r.json()["trace_id"] == "abc123"
        assert r.headers["X-Trace-Id"] == "abc123"

        r2 = client.get("/t")
        assert r2.json()["trace_id"]
        assert r2.headers["X-Trace-Id"] == r2.json()["trace_id"]


class TestAppSkeleton:
    def test_root_metadata(self) -> None:
        client = TestClient(create_app())
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["service"] == "bot_chain_api"

    def test_playground_redirects_to_docs(self) -> None:
        client = TestClient(create_app())
        r = client.get("/playground", follow_redirects=False)
        assert r.status_code in (302, 307)
        assert r.headers["Location"].endswith("/docs")

    def test_docs_served_locally(self) -> None:
        client = TestClient(create_app())
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200

    def test_new_account_shape_matches_web3(self) -> None:
        acct = Account.create()
        assert acct.address == Web3.to_checksum_address(acct.address)


class TestKeystore:
    def test_create_and_sign_roundtrip(self, tmp_path) -> None:

        ks = Keystore(tmp_path, secret=KS_MATERIAL_RIGHT)
        acct = ks.create()
        assert ks.get(acct.address) is not None
        assert ks.get(acct.address).address == acct.address
        assert ks.reveal(acct.address).startswith("0x")

    def test_persisted_file_written(self, tmp_path) -> None:

        ks = Keystore(tmp_path, secret=KS_MATERIAL_RIGHT)
        acct = ks.create()
        assert acct.persisted is True
        assert (tmp_path / f"{acct.address}.json").exists()
        content = (tmp_path / f"{acct.address}.json").read_text(encoding="utf-8")
        assert acct.address in content
        assert "private" not in content.lower() or "ciphertext" in content

    def test_ephemeral_mode_not_persisted(self, tmp_path) -> None:

        ks = Keystore(tmp_path, secret=None)
        acct = ks.create()
        assert acct.persisted is False
        assert not (tmp_path / f"{acct.address}.json").exists()

    def test_unknown_address_returns_none(self, tmp_path) -> None:

        ks = Keystore(tmp_path, secret=KS_MATERIAL_RIGHT)
        assert ks.get(ACCOUNT_B) is None

    def test_wrong_secret_raises(self, tmp_path) -> None:

        Keystore(tmp_path, secret=KS_MATERIAL_RIGHT).create()
        with pytest.raises(ServiceError):
            Keystore(tmp_path, secret=KS_MATERIAL_WRONG).get(
                next(iter(tmp_path.glob("*.json"))).stem
            )


class TestExplorer:
    def test_stats_and_passthrough(self) -> None:
        with respx.mock:
            base = EXPLORER_BASE
            respx.get(f"{base}/stats").mock(return_value=Response(200, json={"a": 1}))
            respx.get(f"{base}/addresses/{ACCOUNT_A}/transactions").mock(
                return_value=Response(200, json={"items": [{"hash": "0x1"}]})
            )
            respx.get(f"{base}/tokens/{USDT}/holders").mock(
                return_value=Response(200, json={"items": [], "next_page_params": {"k": "v"}})
            )
            client = ExplorerClient(httpx.Client(base_url=base))
            assert client.stats() == {"a": 1}
            assert client.address_transactions(ACCOUNT_A)["items"]
            assert client.token_holders(USDT)["next_page_params"] == {"k": "v"}
            client._http.close()

    def test_http_error_maps_to_chain_error(self) -> None:
        with respx.mock:
            respx.get(f"{EXPLORER_BASE}/stats").mock(return_value=Response(500, text="boom"))
            client = ExplorerClient(httpx.Client(base_url=EXPLORER_BASE))
            with pytest.raises(ChainError):
                client.stats()
            client._http.close()
