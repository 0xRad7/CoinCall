"""T17：SDK 签名 digest 与 P0 冻结口径一致（黄金向量逐字节锁死）。

向量文件：`../coincall-contracts/vectors/eip712_golden.json`（第三份独立实现的比对基准）。
文件不在场 → skip（绑定索引 TEST-20261005221052 T17 行）。

anvil 账户 #0 私钥为公开测试密钥（无价值），仅用于验签侧确定性签名比对。
"""

import json
from pathlib import Path

import pytest
from eth_utils import keccak, to_checksum_address

from coincall.signing import (
    PAY_VAULT_ADDRESS,
    Authorization,
    build_payment_header,
    domain_separator,
    eip712_digest,
    recover_signer,
    sign_authorization,
)

VECTOR_PATH = (
    Path(__file__).resolve().parents[2] / "coincall-contracts" / "vectors" / "eip712_golden.json"
)
# anvil 账户 #0（公开助记词派生，无价值）：向量 address 字段即该账户
ANVIL0_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _load_vector() -> dict:
    if not VECTOR_PATH.exists():
        pytest.skip(f"黄金向量不在场: {VECTOR_PATH}")
    return json.loads(VECTOR_PATH.read_text())


def _to_auth(message: dict) -> Authorization:
    return Authorization(
        from_=message["from"],
        to=message["to"],
        value=int(message["value"]),
        valid_after=int(message["validAfter"]),
        valid_before=int(message["validBefore"]),
        nonce=bytes.fromhex(message["nonce"][2:]),
    )


@pytest.mark.unit
def test_golden_vector() -> None:
    """T17：domainSeparator / structHash / digest 逐字节一致 + recover==address。"""
    vector = _load_vector()
    domain = vector["domain"]
    for case in vector["cases"]:
        auth = _to_auth(case["message"])
        vc = domain["verifyingContract"]
        chain_id = int(domain["chainId"])
        assert domain_separator(vc, chain_id) == bytes.fromhex(case["domainSeparator"][2:])
        ds = domain_separator(vc, chain_id)
        assert eip712_digest(auth, vc, chain_id) == bytes.fromhex(case["digest"][2:])
        # digest 的两段构成也可独立比对
        assert keccak(b"\x19\x01" + ds + bytes.fromhex(case["structHash"][2:])) == bytes.fromhex(
            case["digest"][2:]
        )
        # 验签侧：向量签名在我的 digest 上恢复出向量地址
        sig = case["signature"]
        recovered = recover_signer(
            bytes.fromhex(case["digest"][2:]),
            v=int(sig["v"]),
            r=sig["r"],
            s=sig["s"],
        )
        assert recovered == to_checksum_address(case["address"])


@pytest.mark.unit
def test_golden_vector_deterministic_signature() -> None:
    """RFC6979 确定性签名：本地签出的 v/r/s 与向量签名逐字段一致。"""
    vector = _load_vector()
    domain = vector["domain"]
    for case in vector["cases"]:
        auth = _to_auth(case["message"])
        sig = sign_authorization(
            auth,
            private_key=bytes.fromhex(ANVIL0_KEY[2:]),
            verifying_contract=domain["verifyingContract"],
            chain_id=int(domain["chainId"]),
        )
        assert sig.v == case["signature"]["v"]
        assert sig.r == case["signature"]["r"]
        assert sig.s == case["signature"]["s"]


@pytest.mark.unit
def test_digest_binds_to_verifying_contract() -> None:
    """digest 绑定 verifyingContract：占位地址与真实 PayVault 的 digest 必须不同。"""
    auth = Authorization(
        from_="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        to="0x000000000000000000000000000000000000dEaD",
        value=10000,
        valid_after=0,
        valid_before=4102444800,
        nonce=bytes(32),
    )
    dead = eip712_digest(auth, "0x000000000000000000000000000000000000dEaD", 968)
    real = eip712_digest(auth, PAY_VAULT_ADDRESS, 968)
    assert dead != real
    # 链 ID 同样参与绑定
    assert eip712_digest(auth, PAY_VAULT_ADDRESS, 967) != real


@pytest.mark.unit
def test_build_payment_header_aliases_and_signature() -> None:
    """X-PAYMENT 头：base64(JSON)，字段别名 from/validAfter/validBefore + v/r/s。"""
    import base64

    auth = Authorization(
        from_="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",  # anvil #0（= 签名 key 持有者）
        to=PAY_VAULT_ADDRESS,
        value=10000,
        valid_after=1,
        valid_before=2,
        nonce=bytes.fromhex("11" * 32),
    )
    key = bytes.fromhex(ANVIL0_KEY[2:])
    sig = sign_authorization(
        auth, private_key=key, verifying_contract=PAY_VAULT_ADDRESS, chain_id=968
    )
    header = build_payment_header(auth, sig)
    payload = json.loads(base64.b64decode(header))
    assert payload["from"] == auth.from_
    assert payload["to"] == auth.to
    assert payload["value"] == "10000"
    assert payload["validAfter"] == 1
    assert payload["validBefore"] == 2
    assert payload["nonce"] == "0x" + "11" * 32
    assert payload["v"] == sig.v and payload["r"] == sig.r and payload["s"] == sig.s
    assert set(payload) == {
        "from",
        "to",
        "value",
        "validAfter",
        "validBefore",
        "nonce",
        "v",
        "r",
        "s",
    }
    # 头可直接被网关语义恢复：签名者 == 授权人
    assert (
        recover_signer(
            eip712_digest(auth, PAY_VAULT_ADDRESS, 968),
            v=payload["v"],
            r=payload["r"],
            s=payload["s"],
        )
        == auth.from_
    )
