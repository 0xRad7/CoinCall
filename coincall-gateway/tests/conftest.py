"""CoinCall gateway 单测夹具：全部依赖可注入 fake，DuckDB 用 tmp_path（零网络）。"""

import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from eth_keys import keys
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app
from app.modules.auth import ApiKeyInfo, AuthError, CoreAuthClient
from app.modules.manifest_client import ManifestInfo

pytestmark = pytest.mark.unit

#: 黄金向量固定参数（tests/vectors/eip712_golden_gateway.json）
VAULT = "0x000000000000000000000000000000000000dEaD"
CHAIN_ID = 968
CONSUMER_WALLET = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
#: 黄金向量同源账户（mnemonic "abandon…about" / m/44'/60'/0'/0/0）的私钥，仅测试使用
CONSUMER_PRIVATE_KEY = keys.PrivateKey(
    bytes.fromhex("1ab42cc412b618bdea3a599e3c9bae199ebf030895b039e9db1e30dafb12b727")
)
PROVIDER_WALLET = "0x1234567890AbCdEf1234567890aBcDeF12345678"

API_KEY = "cck_unit_test_key_0000000000000001"


def make_manifest(service_id: str = "svc_translate_v1", **overrides: Any) -> ManifestInfo:
    manifest = {
        "service_id": service_id,
        "name": "中英技术文档翻译",
        "version": "1.0.0",
        "provider": {
            "agent_id": 137,
            "wallet": PROVIDER_WALLET,
            "display_name": "Team booth-demo",
        },
        "endpoint": {"type": "internal", "handler": "echo", "timeout_ms": 30000},
        "pricing": {"model": "per_call", "amount": "0.01", "amount_raw": "10000", "token": "USDT"},
        "chain": {"network": CHAIN_ID},
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "output_schema": {"type": "object"},
        "status": "active",
        "manifest_hash": "sha256:" + "0" * 64,
    }
    manifest.update(overrides)
    return ManifestInfo(
        service_id=service_id,
        manifest=manifest,  # type: ignore[arg-type]
        status=str(manifest.get("status", "active")),
        manifest_hash=str(manifest.get("manifest_hash", "")),
    )


def make_x_payment_header(
    *,
    value: str = "10000",
    to: str = VAULT,
    nonce: str = "0x" + "00" * 31 + "01",
    valid_after: int = 0,
    valid_before: int = 2_000_000_000,
    signer: keys.PrivateKey | None = None,
    from_addr: str = CONSUMER_WALLET,
    vault: str = VAULT,
    chain_id: int = CHAIN_ID,
) -> str:
    """用 app 的 digest 构造 + eth_keys 签名，产出 X-PAYMENT 头。"""
    from app.core.payment import Authorization, XPayment, eip712_digest, recover_signer

    auth = Authorization(
        from_=from_addr,
        to=to,
        value=value,
        valid_after=valid_after,
        valid_before=valid_before,
        nonce=nonce,
    )
    digest = eip712_digest(auth, vault, chain_id)
    key = signer or CONSUMER_PRIVATE_KEY
    sig = key.sign_msg_hash(digest)
    payment = XPayment(
        **auth.model_dump(by_alias=True),
        v=sig.v + 27,
        r="0x" + sig.r.to_bytes(32, "big").hex(),
        s="0x" + sig.s.to_bytes(32, "big").hex(),
    )
    if signer is None:  # 默认签名者自检；显式传入异签者时跳过（负路径用例）
        assert recover_signer(payment, vault, chain_id).lower() == from_addr.lower()
    payload = json.loads(payment.model_dump_json(by_alias=True))
    return base64.b64encode(json.dumps(payload).encode()).decode()


class FakeChain:
    """ChainAdapter fake：预置余额/授权，记录调用。"""

    def __init__(self, balance: int = 10**12, allowance: int = 10**12) -> None:
        self.balance = balance
        self.allowance = allowance
        self.calls: list[tuple[str, str, str]] = []

    async def erc20_balance(self, wallet: str, token: str, *, force: bool = False) -> int:
        self.calls.append(("balance", wallet, token))
        return self.balance

    async def erc20_allowance(
        self, owner: str, spender: str, token: str, *, force: bool = False
    ) -> int:
        self.calls.append(("allowance", owner, spender))
        return self.allowance


class FakeAuth:
    """CoreAuthClient fake：内存 key 表。"""

    def __init__(self, keys_by_key: dict[str, ApiKeyInfo] | None = None) -> None:
        self.keys = (
            keys_by_key
            if keys_by_key is not None
            else {
                API_KEY: ApiKeyInfo(
                    key_id="key_unit1",
                    consumer_wallet=CONSUMER_WALLET,
                    quota_raw=None,
                    status="active",
                )
            }
        )

    async def validate(self, api_key: str) -> ApiKeyInfo:
        info = self.keys.get(api_key)
        if info is None:
            raise AuthError(code="apikey_unknown", detail="api key 不存在")
        if info.status != "active":
            raise AuthError(code="apikey_revoked", detail=f"api key 已{info.status}")
        return info


class FakeManifests:
    """ManifestClient fake：内存目录。"""

    def __init__(self, manifests: dict[str, ManifestInfo] | None = None) -> None:
        self.manifests = manifests or {"svc_translate_v1": make_manifest()}

    async def get(self, service_id: str) -> ManifestInfo:
        found = self.manifests.get(service_id)
        if found is None:
            from app.modules.manifest_client import ServiceNotFoundError

            raise ServiceNotFoundError(service_id)
        return found


class FixedProvider:
    """ProviderAdapter fake：固定响应或固定失败。"""

    def __init__(
        self, status_code: int = 200, body: Any = None, error: Exception | None = None
    ) -> None:
        self.status_code = status_code
        self.body = body if body is not None else {"result": "translated"}
        self.error = error
        self.forwarded: list[dict[str, Any]] = []

    async def forward(
        self, manifest: Any, body: Any, upstream_headers: dict[str, str] | None = None
    ) -> Any:
        self.last_upstream_headers = upstream_headers

        from app.modules.providers import ProviderResult

        self.forwarded.append({"service_id": manifest.service_id, "body": body})
        if self.error is not None:
            raise self.error
        return ProviderResult(status_code=self.status_code, body=self.body)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        duckdb_path=str(tmp_path / "gateway.duckdb"),
        pay_vault_address=VAULT,
        chain_id=CHAIN_ID,
        keeper_enabled=False,  # 单测零后台任务（A2）；keeper 用例显式注入实例
    )


@asynccontextmanager
async def gateway_serve(
    settings: Settings,
    *,
    chain: FakeChain | None = None,
    auth: FakeAuth | None = None,
    manifests: FakeManifests | None = None,
    providers: dict[str, Any] | None = None,
    keeper: Any | None = None,
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    """组装带 fake 依赖的应用并进入 lifespan（ASGITransport 不自动跑 lifespan）。"""
    app = create_app(
        settings,
        chain_adapter=chain or FakeChain(),
        auth_client=auth or FakeAuth(),
        manifest_client=manifests or FakeManifests(),
        providers=providers or {"internal": FixedProvider(), "http_json": FixedProvider()},
        keeper=keeper,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
            yield client, app


def call_headers(
    *,
    api_key: str = API_KEY,
    payment: str | None = None,
    idempotency: str | None = None,
    **payment_kwargs: Any,
) -> dict[str, str]:
    headers = {"X-Api-Key": api_key}
    if payment is None and "skip_payment" not in payment_kwargs:
        headers["X-PAYMENT"] = make_x_payment_header(
            **{k: v for k, v in payment_kwargs.items() if k != "skip_payment"}
        )
    elif payment is not None:
        headers["X-PAYMENT"] = payment
    if idempotency:
        headers["X-Idempotency-Key"] = idempotency
    return headers


def core_auth_client_for_respx() -> CoreAuthClient:
    """给 respx 用例的真实 CoreAuthClient（指向 mock 的 base url）。"""
    return CoreAuthClient(base_url="http://core.test", http=httpx.AsyncClient())
