"""EIP-712 支付授权签名（本仓独立实现，T17 黄金向量逐字节锁死）。

> 冻结契约点（CONSTRAINTS A3）：digest 手工编码（纯静态 abi.encode），
> 不 import 网关/合约仓任何代码；与 PayVault 合约 / 网关验签侧三方一致，
> 基准向量 `../coincall-contracts/vectors/eip712_golden.json`。

域与 PayVault 一致：name "PayVault" / version "1" / chainId 968，
verifyingContract = 已部署的 PayVault（测试网 968）。

Authorization 六元组（对齐 x402 / EIP-3009 TransferWithAuthorization）：
    Authorization(address from, address to, uint256 value,
                  uint256 validAfter, uint256 validBefore, bytes32 nonce)

X-PAYMENT 头 = base64(JSON{ from, to, value, validAfter, validBefore, nonce, v, r, s })。
"""

import base64
import json
from dataclasses import dataclass

from eth_keys import keys as eth_keys
from eth_utils import keccak, to_checksum_address

DOMAIN_NAME = "PayVault"
DOMAIN_VERSION = "1"
CHAIN_ID = 968
PAY_VAULT_ADDRESS = "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471"

WORD_SIZE = 32  # EVM 字（地址左补齐与 nonce/私钥长度共用）

DOMAIN_TYPEHASH: bytes = keccak(
    b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
AUTHORIZATION_TYPEHASH: bytes = keccak(
    b"Authorization(address from,address to,uint256 value,"
    b"uint256 validAfter,uint256 validBefore,bytes32 nonce)"
)


@dataclass(frozen=True)
class Authorization:
    """支付授权六元组（value 为最小单位整数；网关侧序列化为十进制字符串）。"""

    from_: str
    to: str
    value: int
    valid_after: int
    valid_before: int
    nonce: bytes

    def __post_init__(self) -> None:
        if len(self.nonce) != WORD_SIZE:
            raise ValueError(f"nonce 必须为 32 字节，得到 {len(self.nonce)}")
        object.__setattr__(self, "from_", to_checksum_address(self.from_))
        object.__setattr__(self, "to", to_checksum_address(self.to))
        if self.valid_before <= self.valid_after:
            raise ValueError("valid_before 必须晚于 valid_after")


@dataclass(frozen=True)
class PaymentSignature:
    """签名三元组（v 归一为 27|28，r/s 为 0x 小写 hex）。"""

    v: int
    r: str
    s: str

    def __post_init__(self) -> None:
        if self.v not in (27, 28):
            raise ValueError(f"v 必须为 27|28: {self.v}")


def _pad_uint(n: int) -> bytes:
    return n.to_bytes(32, "big")


def _pad_address(addr: str) -> bytes:
    return int(addr, 16).to_bytes(32, "big")


def domain_separator(verifying_contract: str, chain_id: int) -> bytes:
    """EIP-712 domainSeparator（黄金向量锁定：2ef5b3070e03…）。"""
    return keccak(
        DOMAIN_TYPEHASH
        + keccak(DOMAIN_NAME.encode())
        + keccak(DOMAIN_VERSION.encode())
        + _pad_uint(chain_id)
        + _pad_address(verifying_contract)
    )


def authorization_struct_hash(auth: Authorization) -> bytes:
    """Authorization 结构哈希（静态字段手工 abi.encode，零 eth-abi 依赖）。"""
    return keccak(
        AUTHORIZATION_TYPEHASH
        + _pad_address(auth.from_)
        + _pad_address(auth.to)
        + _pad_uint(auth.value)
        + _pad_uint(auth.valid_after)
        + _pad_uint(auth.valid_before)
        + auth.nonce
    )


def eip712_digest(auth: Authorization, verifying_contract: str, chain_id: int) -> bytes:
    """\\x19\\x01 + domainSeparator + structHash（黄金向量：65a25c5ed41256b0…）。"""
    return keccak(
        b"\x19\x01"
        + domain_separator(verifying_contract, chain_id)
        + authorization_struct_hash(auth)
    )


def sign_authorization(
    auth: Authorization,
    *,
    private_key: bytes,
    verifying_contract: str,
    chain_id: int,
) -> PaymentSignature:
    """本地私钥对 digest 签名（RFC6979 确定性；私钥只进本进程，不出网络）。"""
    if len(private_key) != WORD_SIZE:
        raise ValueError(f"私钥必须为 32 字节，得到 {len(private_key)}")
    digest = eip712_digest(auth, verifying_contract, chain_id)
    sig = eth_keys.PrivateKey(private_key).sign_msg_hash(digest)
    return PaymentSignature(
        v=sig.v + 27,
        r="0x" + sig.r.to_bytes(32, "big").hex(),
        s="0x" + sig.s.to_bytes(32, "big").hex(),
    )


def recover_signer(digest: bytes, *, v: int, r: str, s: str) -> str:
    """ecrecover(digest) → checksum 地址（本地恢复，无网络）。"""
    signature = eth_keys.Signature(vrs=(v - 27 if v in (27, 28) else v, int(r, 16), int(s, 16)))
    public = signature.recover_public_key_from_msg_hash(digest)
    return to_checksum_address(public.to_address())


def build_payment_header(auth: Authorization, sig: PaymentSignature) -> str:
    """X-PAYMENT 头：base64(JSON{六元组 + v/r/s})，字段别名 from/validAfter/validBefore。"""
    payload = {
        "from": auth.from_,
        "to": auth.to,
        "value": str(auth.value),
        "validAfter": auth.valid_after,
        "validBefore": auth.valid_before,
        "nonce": "0x" + auth.nonce.hex(),
        "v": sig.v,
        "r": sig.r,
        "s": sig.s,
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()
