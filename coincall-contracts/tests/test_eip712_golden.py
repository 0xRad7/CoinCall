"""T8：黄金向量消费——锁死 EIP-712 digest 字节级口径（跨仓逐字节比对基准）。

三层断言：
① Python 参考实现从 JSON 字段重建 digest == 向量记录值（全部用例）；
② recover_signer(digest, v,r,s) == 向量地址（ecrecover 语义）；
③ 合约侧：向量账户在部署合约真实域下重签同一 Authorization，
   chargeWithSigBatch 验签通过且 Charged.from == 向量地址——即"合约 ecrecover 结果 == address"。
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from eth_account import Account

from payvault.chain import call_contract
from payvault.eip712 import (
    Authorization,
    authorization_digest,
    authorization_struct_hash,
    domain_separator,
    nonce_from,
    recover_signer,
    sign_authorization,
)
from tests.helpers import charge_call, events_of, submit_charge

VECTORS = Path(__file__).resolve().parent.parent / "vectors" / "eip712_golden.json"


def load_cases() -> list[dict]:
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert data["domain"] == {
        "name": "PayVault",
        "version": "1",
        "chainId": 968,
        "verifyingContract": "0x000000000000000000000000000000000000dEaD",
    }
    return data["cases"]


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return load_cases()


def case_to_auth(case: dict) -> Authorization:
    msg = case["message"]
    return Authorization(
        from_addr=msg["from"],
        to=msg["to"],
        value=int(msg["value"]),
        valid_after=int(msg["validAfter"]),
        valid_before=int(msg["validBefore"]),
        nonce=nonce_from(msg["nonce"]),
    )


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: str(c.get("description")))
def test_digest_reproducible_from_fields(case: dict) -> None:
    """① 参考实现重建 digest 与冻结向量逐字节一致。"""
    digest = authorization_digest(case_to_auth(case), 968, case["message"]["to"])
    assert f"0x{digest.hex()}" == case["digest"]


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: str(c.get("description")))
def test_domain_separator_and_struct_hash(case: dict) -> None:
    """domainSeparator/structHash 分量也逐字节一致（网关排查分歧时定位用）。"""
    assert f"0x{domain_separator(968, case['message']['to']).hex()}" == case["domainSeparator"]
    assert f"0x{authorization_struct_hash(case_to_auth(case)).hex()}" == case["structHash"]


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: str(c.get("description")))
def test_recover_signer_from_vector(case: dict) -> None:
    """② Python ecrecover：digest+v/r/s 恢复出向量地址。"""
    sig = case["signature"]
    recovered = recover_signer(
        bytes.fromhex(case["digest"][2:]),
        int(sig["v"]),
        bytes.fromhex(sig["r"][2:]),
        bytes.fromhex(sig["s"][2:]),
    )
    assert recovered == case["address"]
    # 非法签名（篡改 v）恢复失败返回 None，对齐合约 ecrecover==0 分支
    assert (
        recover_signer(
            bytes.fromhex(case["digest"][2:]),
            29,
            bytes.fromhex(sig["r"][2:]),
            bytes.fromhex(sig["s"][2:]),
        )
        is None
    )


def test_contract_ecrecover_matches_vector_address(vault_env) -> None:
    """③ 合约侧消费：向量账户按部署合约真实域重签同一 message，Charged.from == 向量地址。"""
    Account.enable_unaudited_hdwallet_features()
    vector_account = Account.from_mnemonic(
        "test test test test test test test test test test test junk",
        account_path="m/44'/60'/0'/0/0",
    )
    assert vector_account.address == load_cases()[0]["address"]  # 派生口径与向量一致

    case = load_cases()[0]
    # 给向量账户垫付 gas + 代币 + 授权（成为"消费者"）
    w3 = vault_env.w3
    w3.eth.send_transaction(
        {"from": w3.eth.accounts[0], "to": vector_account.address, "value": 10**18}
    )
    mock, vault = vault_env.mock_usdt, vault_env.vault
    call_contract(w3, vault_env.operator, mock.functions.mint(vector_account.address, 10**6))
    call_contract(w3, vector_account, mock.functions.approve(vault.address, 2**256 - 1))

    signed = sign_authorization(
        vector_account,
        replace(case_to_auth(case), to=vault.address),
        vault_env.chain_id,
        vault.address,
    )
    receipt = submit_charge(
        w3, vault_env.operator, vault, [charge_call(signed, vault_env.provider.address)]
    )
    charged = events_of(vault, receipt, "Charged")
    assert len(charged) == 1
    assert charged[0]["from"] == case["address"]
    assert charged[0]["value"] == int(case["message"]["value"])
    assert charged[0]["nonce"] == case["message"]["nonce"]  # 事件侧 bytes→hex 与向量口径一致


def test_cross_domain_signature_rejected(vault_env) -> None:
    """向量字面签名（域=0x…dead、to=0x…dead）拿到本地部署合约上必须失败且资金零变动。

    合约先验 to==address(this)（auth_to_mismatch），轮不到验签——签名与合约地址天然绑定。
    """
    case = load_cases()[0]
    sig = case["signature"]
    literal = {
        "provider": vault_env.provider.address,
        "auth": case["message"],
        "v": int(sig["v"]),
        "r": bytes.fromhex(sig["r"][2:]),
        "s": bytes.fromhex(sig["s"][2:]),
    }
    receipt = submit_charge(vault_env.w3, vault_env.operator, vault_env.vault, [literal])
    charged = events_of(vault_env.vault, receipt, "Charged")
    failed = events_of(vault_env.vault, receipt, "ChargeFailed")
    assert charged == []
    assert [str(f["reason"]) for f in failed] == ["auth_to_mismatch"]
    assert vault_env.vault.functions.totalCredits().call() == 0
