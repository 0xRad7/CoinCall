"""T18：SDK 三路测试 success / 402 带指引 / aborted 从未扣款（03 §7-3）。

全部经 httpx MockTransport（A2 零网络）；网关响应形态对齐 coincall-gateway
`app/core/schemes.py::build_challenge` 与 `app/modules/call_route.py` 的真实输出。
"""

import base64
import json
from collections.abc import Callable

import httpx
import pytest
from fake_chain import FakeChain

from coincall.client import Client
from coincall.errors import (
    CoinCallError,
    GatewayError,
    PaymentRequiredError,
    WalletError,
)
from coincall.policy import PolicyViolationError
from coincall.signing import PAY_VAULT_ADDRESS, eip712_digest, recover_signer
from coincall.wallet import LocalWallet

ANVIL1_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
ANVIL1_ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

PRICE_RAW = 10000
CATALOG = {
    "services": [
        {
            "service_id": "svc_e2e_demo",
            "manifest": {"pricing": {"amount": "0.01", "amount_raw": "10000", "token": "USDT"}},
            "status": "active",
        }
    ],
    "count": 1,
}
RECEIPT_HEADERS = {
    "X-Receipt-Id": "rcp_abc123",
    "X-Charged-Raw": "10000",
    "X-Receipt-Sig": "deadbeef" * 8,
}


def challenge(code: str, detail: str, balance_raw: str = "0") -> dict:
    """网关 402 质询形态（对齐 PayVaultScheme.build_challenge）。"""
    return {
        "error": "payment_required",
        "detail": detail,
        "code": code,
        "service_id": "svc_e2e_demo",
        "pricing": {"amount": "0.01", "amount_raw": "10000", "token": "USDT"},
        "wallet_balance_raw": balance_raw,
        "payment": {
            "scheme": "erc3009-vault",
            "header": "X-PAYMENT",
            "domain": {
                "name": "PayVault",
                "version": "1",
                "chainId": 968,
                "verifyingContract": PAY_VAULT_ADDRESS,
            },
            "approve_to": PAY_VAULT_ADDRESS,
        },
        "trace_id": "t1",
    }


class Env:
    """单文件装配：mock core+gateway + 请求捕获。"""

    def __init__(self, gateway_factory: Callable[[], httpx.Response]) -> None:
        self.gateway_requests: list[httpx.Request] = []
        self.gateway_factory = gateway_factory
        self.catalog_requests: list[httpx.Request] = []
        self.catalog_etag = '"v1"'

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "core.test":
                self.catalog_requests.append(request)
                if request.headers.get("If-None-Match") == self.catalog_etag:
                    return httpx.Response(304, headers={"ETag": self.catalog_etag})
                return httpx.Response(200, json=CATALOG, headers={"ETag": self.catalog_etag})
            self.gateway_requests.append(request)
            return self.gateway_factory()

        self.http = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
        self.wallet = LocalWallet.from_key(ANVIL1_KEY, chain=FakeChain())

    def client(self, **kwargs: object) -> Client:
        return Client(
            api_key="cck_test",
            wallet=self.wallet,
            gateway_url="http://gw.test",
            core_url="http://core.test",
            http=self.http,
            **kwargs,  # type: ignore[arg-type] —— 测试专用的窄化透传
        )

    def captured_payment(self) -> dict:
        header = self.gateway_requests[-1].headers["X-PAYMENT"]
        return json.loads(base64.b64decode(header))


# -- 路径一：success --


@pytest.mark.unit
def test_call_success_signs_and_passes_receipt_through() -> None:
    env = Env(lambda: httpx.Response(200, json={"echo": "hi"}, headers=RECEIPT_HEADERS))
    c = env.client()
    result = c.call("svc_e2e_demo", {"text": "hi"})
    assert result.status_code == 200
    assert result.body == {"echo": "hi"}
    assert result.receipt_id == "rcp_abc123"
    assert result.charged_raw == "10000"
    assert result.receipt_sig == "deadbeef" * 8
    assert c.spent_raw == PRICE_RAW

    # 请求头组装：X-Api-Key + X-PAYMENT（可被网关语义独立恢复）+ 幂等键
    req = env.gateway_requests[0]
    assert req.headers["X-Api-Key"] == "cck_test"
    payload = env.captured_payment()
    assert payload["from"] == ANVIL1_ADDR
    assert payload["to"] == PAY_VAULT_ADDRESS
    assert payload["value"] == "10000"
    assert len(bytes.fromhex(payload["nonce"][2:])) == 32
    assert payload["validBefore"] - payload["validAfter"] == 600
    digest = eip712_digest(_auth_from_payload(payload), PAY_VAULT_ADDRESS, 968)
    assert recover_signer(digest, v=payload["v"], r=payload["r"], s=payload["s"]) == ANVIL1_ADDR
    assert req.headers["X-Idempotency-Key"]  # 自动幂等键（参数 hash）


@pytest.mark.unit
def test_idempotency_key_is_deterministic_on_params() -> None:
    env = Env(lambda: httpx.Response(200, json={"ok": 1}, headers=RECEIPT_HEADERS))
    c = env.client()
    c.call("svc_e2e_demo", {"text": "hi"})
    k1 = env.gateway_requests[-1].headers["X-Idempotency-Key"]
    k2 = env.gateway_requests[-1].headers["X-Idempotency-Key"]
    assert k1 is not None and k1 == k2  # 同参数重试同键 → 网关可重放防双扣
    c.call("svc_e2e_demo", {"text": "different"})
    k3 = env.gateway_requests[-1].headers["X-Idempotency-Key"]
    assert k3 != k2  # 不同参数不同键（网关 409 语义的前提）


# -- 路径二：402 → PaymentRequiredError（人话指引） --


@pytest.mark.unit
def test_call_402_insufficient_balance_guidance() -> None:
    env = Env(
        lambda: httpx.Response(402, json=challenge("insufficient_balance", "钱包余额不足", "2000"))
    )
    c = env.client()
    with pytest.raises(PaymentRequiredError) as exc_info:
        c.call("svc_e2e_demo", {"text": "hi"})
    err = exc_info.value
    assert err.code == "insufficient_balance"
    assert err.amount_raw == "10000"
    assert err.deficit_raw == "8000"
    assert err.approve_to == PAY_VAULT_ADDRESS
    assert "8000" in err.guidance and "MockUSDT" in err.guidance
    assert c.spent_raw == 0  # 402 不进预算


@pytest.mark.unit
def test_call_402_insufficient_allowance_guidance() -> None:
    env = Env(
        lambda: httpx.Response(
            402, json=challenge("insufficient_allowance", "授权额度不足", "999999")
        )
    )
    c = env.client()
    with pytest.raises(PaymentRequiredError) as exc_info:
        c.call("svc_e2e_demo", {"text": "hi"})
    guidance = exc_info.value.guidance
    assert "approve_vault" in guidance and PAY_VAULT_ADDRESS in guidance


@pytest.mark.unit
def test_call_402_malformed_challenge_still_raises() -> None:
    env = Env(lambda: httpx.Response(402, json={"error": "payment_required"}))  # 无 payment 块
    with pytest.raises(PaymentRequiredError):
        env.client().call("svc_e2e_demo", {"text": "hi"})


# -- 路径三：aborted（provider 失败 → 网关 502；从未扣款） --


@pytest.mark.unit
def test_call_aborted_never_charges_budget() -> None:
    env = Env(
        lambda: httpx.Response(
            502,
            json={"error": "service_error", "detail": "provider down", "code": "provider_failed"},
        )
    )
    c = env.client()
    with pytest.raises(GatewayError) as exc_info:
        c.call("svc_e2e_demo", {"text": "hi"})
    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "provider_failed"
    assert c.spent_raw == 0  # aborted：网关零扣款，SDK 预算也零扣减
    # 预算内还有余量可继续重试（aborted 不占用预算）
    env.gateway_factory = lambda: httpx.Response(200, json={"ok": 1}, headers=RECEIPT_HEADERS)
    c.call("svc_e2e_demo", {"text": "hi"})
    assert c.spent_raw == PRICE_RAW


# -- 预算（A5：本地执行，超限拒调且不发请求） --


@pytest.mark.unit
def test_budget_rejects_before_any_network_call() -> None:
    env = Env(lambda: httpx.Response(200, json={"ok": 1}, headers=RECEIPT_HEADERS))
    c = env.client(budget_raw=15000)
    c.call("svc_e2e_demo", {"text": "one"})
    assert c.spent_raw == 10000
    gateway_calls_after_first = len(env.gateway_requests)
    with pytest.raises(PolicyViolationError, match="总额"):
        c.call("svc_e2e_demo", {"text": "two"})
    assert c.spent_raw == 10000  # 策略引擎口径与 spent 同步
    assert len(env.gateway_requests) == gateway_calls_after_first  # 未发任何请求


# -- 目录与杂项 --


@pytest.mark.unit
def test_catalog_etag_cache() -> None:
    env = Env(httpx.Response(200, json={}, headers=RECEIPT_HEADERS))
    c = env.client()
    first = c.catalog()
    second = c.catalog()
    assert first == second == CATALOG
    assert len(env.catalog_requests) == 2  # 第二次带 If-None-Match 命中 304
    assert env.catalog_requests[1].headers["If-None-Match"] == '"v1"'
    c.catalog(force=True)
    assert len(env.catalog_requests) == 3


@pytest.mark.unit
def test_unknown_service_raises() -> None:
    env = Env(httpx.Response(200, json={}, headers=RECEIPT_HEADERS))
    with pytest.raises(CoinCallError, match="svc_missing"):
        env.client().call("svc_missing", {})


@pytest.mark.unit
def test_call_without_wallet_raises() -> None:
    env = Env(httpx.Response(200, json={}, headers=RECEIPT_HEADERS))
    c = Client(
        api_key="cck_test",
        wallet=None,
        gateway_url="http://gw.test",
        core_url="http://core.test",
        http=env.http,
    )
    with pytest.raises(WalletError, match="钱包"):
        c.call("svc_e2e_demo", {"text": "hi"})
    assert c.catalog()["count"] == 1  # 目录浏览无需钱包


def _auth_from_payload(payload: dict) -> object:
    from coincall.signing import Authorization

    return Authorization(
        from_=payload["from"],
        to=payload["to"],
        value=int(payload["value"]),
        valid_after=int(payload["validAfter"]),
        valid_before=int(payload["validBefore"]),
        nonce=bytes.fromhex(payload["nonce"][2:]),
    )
