"""生成 EIP-712 黄金向量 vectors/eip712_golden.json（一次性冻结，测试消费不再重签）。

- 账户：公开的 anvil/foundry 测试助记词派生（无私钥价值），路径 m/44'/60'/0'/0/0；
- 域：{name:"PayVault", version:"1", chainId:968, verifyingContract:0x…dead 占位}；
- 签名：eth_account 官方 encode_typed_data 路径；digest 同时用本仓手工实现并断言一致；
- 文件不含任何私钥材料（只含地址），网关仓在汇合阶段逐字节比对。

重跑可再生字节级一致的向量（全部输入为固定常量）。
"""

import json
from pathlib import Path
from typing import Any

from eth_account import Account
from web3 import Web3

from payvault.eip712 import (
    AUTHORIZATION_TYPE_STRING,
    Authorization,
    authorization_digest,
    authorization_struct_hash,
    domain_separator,
    recover_signer,
    sign_authorization,
)

VECTORS_PATH = Path(__file__).resolve().parent.parent / "vectors" / "eip712_golden.json"

GOLDEN_MNEMONIC = "test test test test test test test test test test test junk"  # 公开测试助记词
GOLDEN_DERIVATION_PATH = "m/44'/60'/0'/0/0"
GOLDEN_CHAIN_ID = 968
GOLDEN_VERIFYING_CONTRACT = (
    "0x000000000000000000000000000000000000dEaD"  # 占位：真实合约地址由部署时域承担
)

CASES: tuple[tuple[str, dict[str, int]], ...] = (
    # ① 主向量：1 USDT(6dp)、全时间窗、nonce=31337
    (
        "canonical",
        {"value": 1_000_000, "valid_after": 0, "valid_before": 4102444800, "nonce": 31337},
    ),  # 2100-01-01 UTC
    # ② 大额 + 大 nonce + 非零 validAfter
    (
        "large_value_and_nonce",
        {
            "value": 250_000_000_000,
            "valid_after": 1735689600,
            "valid_before": 4102444800,
            "nonce": 2**200 + 12345,
        },
    ),
    # ③ 零金额边界 + 窄窗
    (
        "zero_value_narrow_window",
        {"value": 0, "valid_after": 1735689601, "valid_before": 4102444799, "nonce": 1},
    ),
)


def build_vectors() -> dict[str, Any]:
    # BIP39 助记词派生（公开测试助记词；eth_account 官方标注该 API 为 unaudited）
    Account.enable_unaudited_hdwallet_features()
    account = Account.from_mnemonic(GOLDEN_MNEMONIC, account_path=GOLDEN_DERIVATION_PATH)
    vault = Web3.to_checksum_address(GOLDEN_VERIFYING_CONTRACT)
    sep = domain_separator(GOLDEN_CHAIN_ID, vault)
    cases: list[dict[str, Any]] = []
    for label, case in CASES:
        auth = Authorization(
            from_addr=account.address,
            to=vault,
            value=case["value"],
            valid_after=case["valid_after"],
            valid_before=case["valid_before"],
            nonce=case["nonce"],
        )
        signed = sign_authorization(account, auth, GOLDEN_CHAIN_ID, vault)
        recovered = recover_signer(signed.digest, signed.v, signed.r, signed.s)
        if recovered != Web3.to_checksum_address(account.address):
            msg = f"向量自验签失败: nonce={case['nonce']}"
            raise RuntimeError(msg)
        cases.append(
            {
                "description": label,
                "message": auth.as_typed_data(),
                "address": signed.address,
                "domainSeparator": Web3.to_hex(sep),
                "structHash": Web3.to_hex(authorization_struct_hash(auth)),
                "digest": Web3.to_hex(signed.digest),
                "signature": {
                    "v": signed.v,
                    "r": Web3.to_hex(signed.r),
                    "s": Web3.to_hex(signed.s),
                },
            }
        )
    return {
        "schema_version": 1,
        "description": (
            "PayVault EIP-712 Authorization 黄金向量：锁死 digest 字节级口径；"
            "网关仓（独立实现的验签侧）在汇合阶段逐字节比对"
        ),
        "chain_id": GOLDEN_CHAIN_ID,
        "account_derivation": {
            "mnemonic": GOLDEN_MNEMONIC,
            "path": GOLDEN_DERIVATION_PATH,
            "note": "公开 anvil 测试助记词，无资金价值；向量文件不含私钥",
        },
        "domain": {
            "name": "PayVault",
            "version": "1",
            "chainId": GOLDEN_CHAIN_ID,
            "verifyingContract": GOLDEN_VERIFYING_CONTRACT,
        },
        "struct_type": AUTHORIZATION_TYPE_STRING,
        "cases": cases,
    }


def main() -> None:
    vectors = build_vectors()
    VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    VECTORS_PATH.write_text(
        json.dumps(vectors, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _, primary = CASES[0]
    digest = authorization_digest(
        Authorization(
            from_addr=vectors["cases"][0]["address"],
            to=GOLDEN_VERIFYING_CONTRACT,
            value=primary["value"],
            valid_after=primary["valid_after"],
            valid_before=primary["valid_before"],
            nonce=primary["nonce"],
        ),
        GOLDEN_CHAIN_ID,
        GOLDEN_VERIFYING_CONTRACT,
    )
    print(f"vectors written: {VECTORS_PATH}")
    print(f"primary digest : {Web3.to_hex(digest)}")


if __name__ == "__main__":
    main()
