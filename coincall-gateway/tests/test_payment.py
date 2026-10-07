"""T11：X-PAYMENT 解析 + 本地 ecrecover 验签；T14：EIP-712 黄金向量（SPEC-D4）。

黄金向量为**独立生成**（固定助记词账户对固定 Authorization 签名），
与 coincall-contracts 侧向量在 W10 汇合时逐字节比对（digest/domain/struct hash/签名）。
"""

import base64
import json
from pathlib import Path

import pytest
from eth_keys import keys

from app.core.payment import (
    AUTHORIZATION_TYPEHASH,
    DEFAULT_CHAIN_ID,
    DEFAULT_PAY_VAULT_ADDRESS,
    DOMAIN_TYPEHASH,
    EIP712_DOMAIN_NAME,
    EIP712_DOMAIN_VERSION,
    Authorization,
    PaymentError,
    XPayment,
    authorization_struct_hash,
    build_x_payment_header,
    eip712_digest,
    eip712_domain_separator,
    parse_x_payment,
    recover_signer,
)
from tests.conftest import CHAIN_ID, CONSUMER_PRIVATE_KEY, CONSUMER_WALLET, VAULT

pytestmark = pytest.mark.unit

VECTOR = json.loads((Path(__file__).parent / "vectors" / "eip712_golden_gateway.json").read_text())


def test_golden_vector() -> None:
    """T14：固定助记词账户 + 固定 Authorization → digest/v/r/s 逐字节锁定。"""
    vec = VECTOR
    auth = Authorization(
        from_=vec["authorization"]["from"],
        to=vec["authorization"]["to"],
        value=vec["authorization"]["value"],
        valid_after=vec["authorization"]["validAfter"],
        valid_before=vec["authorization"]["validBefore"],
        nonce=vec["authorization"]["nonce"],
    )
    assert vec["domain"] == {
        "name": EIP712_DOMAIN_NAME,
        "version": EIP712_DOMAIN_VERSION,
        "chainId": DEFAULT_CHAIN_ID,
        "verifyingContract": DEFAULT_PAY_VAULT_ADDRESS,
    }
    assert "0x" + eip712_domain_separator(VAULT, CHAIN_ID).hex() == vec["domain_separator"]
    assert "0x" + authorization_struct_hash(auth).hex() == vec["struct_hash"]
    digest = eip712_digest(auth, VAULT, CHAIN_ID)
    assert "0x" + digest.hex() == vec["digest"]

    payment = XPayment(
        **auth.model_dump(by_alias=True),
        v=vec["signature"]["v"],
        r=vec["signature"]["r"],
        s=vec["signature"]["s"],
    )
    assert recover_signer(payment, VAULT, CHAIN_ID) == vec["signer"]
    assert vec["signer"] == CONSUMER_WALLET  # 同一测试账户，路由用例复用其签名


def test_typehashes_frozen() -> None:
    assert "0x" + DOMAIN_TYPEHASH.hex() == VECTOR["domain_typehash"]
    assert "0x" + AUTHORIZATION_TYPEHASH.hex() == VECTOR["authorization_typehash"]


def test_parse_roundtrip() -> None:
    header = build_x_payment_header(
        XPayment(
            **Authorization(
                from_=CONSUMER_WALLET,
                to=VAULT,
                value="10000",
                valid_after=0,
                valid_before=2_000_000_000,
                nonce="0x" + "11" * 32,
            ).model_dump(by_alias=True),
            v=27,
            r="0x" + "22" * 32,
            s="0x" + "33" * 32,
        )
    )
    assert base64.b64decode(header)  # 合法 base64
    parsed = parse_x_payment(header)
    assert parsed.model_dump(by_alias=True)["from"] == CONSUMER_WALLET
    assert parsed.to == VAULT
    assert parsed.value == "10000"
    assert parsed.v == 27
    assert parsed.nonce == "0x" + "11" * 32


def test_parse_normalizes_v_zero_one() -> None:
    payload = {
        "from": CONSUMER_WALLET,
        "to": VAULT,
        "value": "1",
        "validAfter": 0,
        "validBefore": 1,
        "nonce": "0x" + "00" * 32,
        "v": 0,
        "r": "0x" + "22" * 32,
        "s": "0x" + "33" * 32,
    }
    parsed = parse_x_payment(base64.b64encode(json.dumps(payload).encode()).decode())
    assert parsed.v in (27, 28)


@pytest.mark.parametrize(
    "payload",
    [
        {},  # 全缺
        {"from": "0x123"},  # 坏地址
        {
            "from": CONSUMER_WALLET,
            "to": VAULT,
            "value": "0x10",  # 非十进制
            "validAfter": 0,
            "validBefore": 1,
            "nonce": "0x" + "00" * 32,
            "v": 27,
            "r": "0x" + "22" * 32,
            "s": "0x" + "33" * 32,
        },
        {
            "from": CONSUMER_WALLET,
            "to": VAULT,
            "value": "1",
            "validAfter": 0,
            "validBefore": 1,
            "nonce": "short",  # 坏 nonce
            "v": 27,
            "r": "0x" + "22" * 32,
            "s": "0x" + "33" * 32,
        },
        {
            "from": CONSUMER_WALLET,
            "to": VAULT,
            "value": "1",
            "validAfter": 0,
            "validBefore": 1,
            "nonce": "0x" + "00" * 32,
            "v": 99,  # 坏 v
            "r": "0x" + "22" * 32,
            "s": "0x" + "33" * 32,
        },
        {
            "from": CONSUMER_WALLET,
            "to": VAULT,
            "value": "1",
            "validAfter": 0,
            "validBefore": 1,
            "nonce": "0x" + "00" * 32,
            "v": 27,
            "r": "0x1234",  # 坏 r
            "s": "0x" + "33" * 32,
        },
    ],
)
def test_parse_rejects_malformed(payload: dict[str, object]) -> None:
    header = base64.b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(PaymentError) as exc_info:
        parse_x_payment(header)
    assert exc_info.value.code == "bad_xpayment"


def test_parse_rejects_not_base64() -> None:
    with pytest.raises(PaymentError):
        parse_x_payment("!!!not-base64!!!")


def test_parse_rejects_not_json() -> None:
    with pytest.raises(PaymentError):
        parse_x_payment(base64.b64encode(b"not json").decode())


def test_digest_binds_every_field_and_domain() -> None:
    base = Authorization(
        from_=CONSUMER_WALLET,
        to=VAULT,
        value="10000",
        valid_after=0,
        valid_before=2_000_000_000,
        nonce="0x" + "00" * 32,
    )
    reference = eip712_digest(base, VAULT, CHAIN_ID)

    other_vault = "0x0000000000000000000000000000000000000001"
    assert eip712_digest(base, other_vault, CHAIN_ID) != reference  # verifyingContract 绑定
    assert eip712_digest(base, VAULT, 1) != reference  # chainId 绑定

    for mutated in (
        base.model_copy(update={"to": other_vault}),
        base.model_copy(update={"value": "10001"}),
        base.model_copy(update={"valid_after": 1}),
        base.model_copy(update={"valid_before": 2_000_000_001}),
        base.model_copy(update={"nonce": "0x" + "00" * 31 + "01"}),
    ):
        assert eip712_digest(mutated, VAULT, CHAIN_ID) != reference


def test_recover_signer_matches_and_mismatches() -> None:
    auth = Authorization(
        from_=CONSUMER_WALLET,
        to=VAULT,
        value="10000",
        valid_after=0,
        valid_before=2_000_000_000,
        nonce="0x" + "aa" * 32,
    )
    digest = eip712_digest(auth, VAULT, CHAIN_ID)
    right = CONSUMER_PRIVATE_KEY.sign_msg_hash(digest)
    payment = XPayment(
        **auth.model_dump(by_alias=True),
        v=right.v + 27,
        r="0x" + right.r.to_bytes(32, "big").hex(),
        s="0x" + right.s.to_bytes(32, "big").hex(),
    )
    assert recover_signer(payment, VAULT, CHAIN_ID) == CONSUMER_WALLET

    wrong_key = keys.PrivateKey(bytes(range(32)))
    wrong = wrong_key.sign_msg_hash(digest)
    bad_payment = XPayment(
        **auth.model_dump(by_alias=True),
        v=wrong.v + 27,
        r="0x" + wrong.r.to_bytes(32, "big").hex(),
        s="0x" + wrong.s.to_bytes(32, "big").hex(),
    )
    assert recover_signer(bad_payment, VAULT, CHAIN_ID) != CONSUMER_WALLET
