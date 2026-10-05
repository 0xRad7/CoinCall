"""冻结契约 #3：payment_scheme Protocol + PayVaultScheme v1 + registry（02 §7.5）。"""

import pytest

from app.core.payment import XPayment, parse_x_payment
from app.core.schemes import (
    SCHEME_REGISTRY,
    PayVaultScheme,
    get_scheme,
)
from tests.conftest import CONSUMER_WALLET, VAULT, FakeChain, make_manifest, make_x_payment_header

pytestmark = pytest.mark.unit


def test_registry_holds_vault_scheme() -> None:
    scheme = get_scheme("erc3009-vault")
    assert isinstance(scheme, PayVaultScheme)
    assert scheme.name == "erc3009-vault"
    assert "erc3009-vault" in SCHEME_REGISTRY


def test_registry_unknown_scheme_raises() -> None:
    with pytest.raises(KeyError):
        get_scheme("eip3009-native")  # V1.5 Base Sepolia 预留（W9）


async def test_build_challenge_shape() -> None:
    scheme = PayVaultScheme(chain=FakeChain())
    manifest = make_manifest()
    challenge = scheme.build_challenge(
        code="insufficient_balance",
        detail="本服务按次计费",
        service_id=manifest.service_id,
        pricing=manifest.manifest.pricing.model_dump(),
        wallet_balance_raw=123,
        pay_to=VAULT,
    )
    assert challenge["error"] == "payment_required"
    assert challenge["code"] == "insufficient_balance"
    assert challenge["service_id"] == "svc_translate_v1"
    assert challenge["pricing"]["amount_raw"] == "10000"
    assert challenge["wallet_balance_raw"] == "123"
    assert challenge["payment"]["scheme"] == "erc3009-vault"
    assert challenge["payment"]["approve_to"].startswith("0x")
    assert challenge["payment"]["domain"]["verifyingContract"].startswith("0x")
    assert challenge["trace_id"] == ""  # 路由层注入


async def test_verify_happy_path() -> None:
    chain = FakeChain(balance=10**9, allowance=10**9)
    scheme = PayVaultScheme(chain=chain)
    payment = parse_x_payment(make_x_payment_header(nonce="0x" + "77" * 32))
    result = await scheme.verify(
        x_payment=payment,
        expected_from=CONSUMER_WALLET,
        expected_value_raw="10000",
        pay_to=VAULT,
    )
    assert result.ok, result.code
    assert result.signer == CONSUMER_WALLET
    assert result.wallet_balance_raw == 10**9
    assert result.wallet_allowance_raw == 10**9


async def test_verify_records_nonce_and_replays_reject() -> None:
    scheme = PayVaultScheme(chain=FakeChain())
    payment = parse_x_payment(make_x_payment_header(nonce="0x" + "66" * 32))
    first = await scheme.verify(
        x_payment=payment,
        expected_from=CONSUMER_WALLET,
        expected_value_raw="10000",
        pay_to=VAULT,
    )
    assert first.ok
    second = await scheme.verify(
        x_payment=payment,
        expected_from=CONSUMER_WALLET,
        expected_value_raw="10000",
        pay_to=VAULT,
    )
    assert not second.ok
    assert second.code == "nonce_replayed"


async def test_verify_chain_failure_fail_closed() -> None:
    class ExplodingChain(FakeChain):
        async def erc20_balance(self, wallet: str, token: str) -> int:
            raise RuntimeError("rpc down")

    scheme = PayVaultScheme(chain=ExplodingChain())
    payment = parse_x_payment(make_x_payment_header(nonce="0x" + "55" * 32))
    result = await scheme.verify(
        x_payment=payment,
        expected_from=CONSUMER_WALLET,
        expected_value_raw="10000",
        pay_to=VAULT,
    )
    assert not result.ok
    assert result.code == "chain_unavailable"


def test_xpayment_is_authorization_plus_vrs() -> None:
    """XPayment 继承 Authorization 六元组（冻结契约 #2 的结构关系）。"""
    payment = parse_x_payment(make_x_payment_header())
    fields = payment.model_dump(by_alias=True)
    assert {"from", "to", "value", "validAfter", "validBefore", "nonce", "v", "r", "s"} == set(
        fields
    )
    assert isinstance(payment, XPayment)
