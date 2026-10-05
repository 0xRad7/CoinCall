"""EIP-712 Authorization 的逐字节 digest 构造与签名（与 PayVault.sol 逐字节一致）。

domain = {name:"PayVault", version:"1", chainId, verifyingContract}
struct = Authorization(address from,address to,uint256 value,
        uint256 validAfter,uint256 validBefore,uint256 nonce)

本模块是 digest 构造的参考实现；``vectors/eip712_golden.json`` 黄金向量锁死字节级口径，
网关仓（独立实现的验签侧）在汇合阶段与本向量逐字节比对。
"""

from dataclasses import dataclass
from typing import Any, cast

import eth_abi
from eth_account.messages import SignableMessage, encode_typed_data
from eth_account.signers.local import LocalAccount
from eth_keys.datatypes import Signature as EthKeysSignature
from eth_typing import HexStr
from eth_utils import to_canonical_address
from web3 import Web3

DOMAIN_NAME = "PayVault"
DOMAIN_VERSION = "1"

DOMAIN_TYPE_STRING = (  # 语义常量，不可折行
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
# 类型字符串是 keccak 输入的语义常量，不可折行；nonce 为 bytes32（EIP-3009 正典，E-1 已整改）
AUTHORIZATION_TYPE_STRING = "Authorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"  # noqa: E501

EIP712_DOMAIN_TYPEHASH = Web3.keccak(text=DOMAIN_TYPE_STRING)
AUTHORIZATION_TYPEHASH = Web3.keccak(text=AUTHORIZATION_TYPE_STRING)


NONCE_BYTES = 32  # bytes32（EIP-3009 正典）


def nonce_from(value: str | bytes) -> bytes:
    """nonce 归一化为 32 字节：接受 0x-hex 或 bytes；长度不符抛 ValueError。"""
    raw = Web3.to_bytes(hexstr=cast("HexStr", value)) if isinstance(value, str) else bytes(value)
    if len(raw) != NONCE_BYTES:
        msg = f"nonce 必须是 32 字节（bytes32），实际 {len(raw)} 字节"
        raise ValueError(msg)
    return raw


@dataclass(frozen=True)
class Authorization:
    """EIP-3009 同构授权六元组（from 为 Python 关键字，字段名用 from_addr）。"""

    from_addr: str
    to: str
    value: int
    valid_after: int
    valid_before: int
    nonce: bytes  # bytes32：EIP-3009 正典口径（见 CONSTRAINTS.md E-1）

    def __post_init__(self) -> None:
        if len(self.nonce) != NONCE_BYTES:
            msg = f"nonce 必须是 32 字节（bytes32），实际 {len(self.nonce)} 字节"
            raise ValueError(msg)

    def as_typed_data(self) -> dict[str, Any]:
        """eth_account encode_typed_data 的 message 视图（库路径交叉验证用）。"""
        return {
            "from": self.from_addr,
            "to": self.to,
            "value": self.value,
            "validAfter": self.valid_after,
            "validBefore": self.valid_before,
            "nonce": Web3.to_hex(self.nonce),
        }


def domain_separator(chain_id: int, verifying_contract: str) -> bytes:
    """keccak(abi.encode(TYPEHASH, keccak(name), keccak(version), chainId, verifyingContract))。

    注意：统一走 eth_abi.encode + keccak——web3 7.16 的 Web3.solidity_keccak 对 address
    字符串值产出错误哈希（见 CONSTRAINTS.md C-01），已用 EIP-712 规范官方示例向量仲裁。
    """
    return Web3.keccak(
        eth_abi.encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                EIP712_DOMAIN_TYPEHASH,
                Web3.keccak(text=DOMAIN_NAME),
                Web3.keccak(text=DOMAIN_VERSION),
                chain_id,
                to_canonical_address(verifying_contract),
            ],
        )
    )


def authorization_struct_hash(auth: Authorization) -> bytes:
    """keccak(abi.encode(TYPEHASH, from, to, value, validAfter, validBefore, nonce))。"""
    return Web3.keccak(
        eth_abi.encode(
            ["bytes32", "address", "address", "uint256", "uint256", "uint256", "bytes32"],
            [
                AUTHORIZATION_TYPEHASH,
                to_canonical_address(auth.from_addr),
                to_canonical_address(auth.to),
                auth.value,
                auth.valid_after,
                auth.valid_before,
                auth.nonce,
            ],
        )
    )


def authorization_digest(auth: Authorization, chain_id: int, verifying_contract: str) -> bytes:
    """\x19\x01 ‖ domainSeparator ‖ structHash（合约 ecrecover 消费的 digest）。"""
    return Web3.keccak(
        b"\x19\x01"
        + domain_separator(chain_id, verifying_contract)
        + authorization_struct_hash(auth)
    )


def to_signable(auth: Authorization, chain_id: int, verifying_contract: str) -> SignableMessage:
    """走 eth_account 官方编码路径构造 SignableMessage（与手工 digest 交叉验证）。"""
    return encode_typed_data(
        full_message={
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "Authorization": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "validAfter", "type": "uint256"},
                    {"name": "validBefore", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                ],
            },
            "primaryType": "Authorization",
            "domain": {
                "name": DOMAIN_NAME,
                "version": DOMAIN_VERSION,
                "chainId": chain_id,
                "verifyingContract": Web3.to_checksum_address(verifying_contract),
            },
            "message": auth.as_typed_data(),
        }
    )


@dataclass(frozen=True)
class SignedAuthorization:
    """签名结果：v/r/s 供 chargeWithSigBatch，digest 供比对。"""

    auth: Authorization
    chain_id: int
    verifying_contract: str
    digest: bytes
    v: int
    r: bytes
    s: bytes
    address: str

    def vrs_tuple(self) -> tuple[int, bytes, bytes]:
        return self.v, self.r, self.s


def sign_authorization(
    account: LocalAccount, auth: Authorization, chain_id: int, verifying_contract: str
) -> SignedAuthorization:
    """用 eth_account 官方路径签名（库正确性兜底），digest 用手工实现并断言一致。"""
    signable = to_signable(auth, chain_id, verifying_contract)
    signed = account.sign_message(signable)
    digest = authorization_digest(auth, chain_id, verifying_contract)
    if bytes(signed.message_hash) != digest:  # pragma: no cover - 字节级漂移即本仓错误
        msg = "手工 digest 与 eth_account 编码不一致（EIP-712 实现漂移）"
        raise AssertionError(msg)
    v = int(signed.v)
    if v in (0, 1):
        v += 27  # ecrecover 口径
    return SignedAuthorization(
        auth=auth,
        chain_id=chain_id,
        verifying_contract=Web3.to_checksum_address(verifying_contract),
        digest=digest,
        v=v,
        r=int(signed.r).to_bytes(32, "big"),
        s=int(signed.s).to_bytes(32, "big"),
        address=Web3.to_checksum_address(account.address),
    )


def recover_signer(digest: bytes, v: int, r: bytes, s: bytes) -> str | None:
    """从 digest+v/r/s 恢复签名者；非法签名返回 None（对齐合约 ecrecover==0 分支）。

    v 兼容 27/28 与 0/1 两种口径（eth_keys 内部用 0/1）。
    """
    try:
        normalized_v = v - 27 if v in (27, 28) else v
        sig = EthKeysSignature(
            vrs=(normalized_v, int.from_bytes(r, "big"), int.from_bytes(s, "big"))
        )
        return Web3.to_checksum_address(sig.recover_public_key_from_msg_hash(digest).to_address())
    except Exception:
        return None


__all__ = [
    "Authorization",
    "SignedAuthorization",
    "authorization_digest",
    "authorization_struct_hash",
    "domain_separator",
    "nonce_from",
    "recover_signer",
    "sign_authorization",
    "to_signable",
]
