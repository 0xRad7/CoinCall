"""冻结契约 #2：X-PAYMENT 解析 + EIP-712 digest 构造 + ecrecover（02 §2/§5a）。

> 契约冻结（08 §2）：签名一经测试冻结不得修改；黄金向量见
> tests/vectors/eip712_golden_gateway.json（SPEC-D4：独立生成，
> 与 coincall-contracts 侧向量在 W10 汇合时逐字节比对）。

EIP-712 域与 PayVault 合约一致：name "PayVault" / version "1" / chainId 968，
verifyingContract 运行期从 env 读（默认 0x…dead 占位，接线真实地址属 W4）。

Authorization 六元组（对齐 x402 / Base USDC EIP-3009 TransferWithAuthorization）：
    Authorization(address from, address to, uint256 value,
                  uint256 validAfter, uint256 validBefore, bytes32 nonce)

X-PAYMENT 头 = base64(JSON{ from, to, value, validAfter, validBefore, nonce, v, r, s })。
digest 手工编码（纯静态 abi.encode，零 eth-abi 依赖），签名/恢复走 eth_keys。
"""

import base64
import binascii
import json
import re
from typing import Any

from eth_keys import keys as eth_keys
from eth_utils import keccak, to_checksum_address
from pydantic import BaseModel, ConfigDict, Field, field_validator

EIP712_DOMAIN_NAME = "PayVault"
EIP712_DOMAIN_VERSION = "1"
DEFAULT_CHAIN_ID = 968
DEFAULT_PAY_VAULT_ADDRESS = "0x000000000000000000000000000000000000dEaD"

DOMAIN_TYPEHASH: bytes = keccak(
    b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
AUTHORIZATION_TYPEHASH: bytes = keccak(
    b"Authorization(address from,address to,uint256 value,"
    b"uint256 validAfter,uint256 validBefore,bytes32 nonce)"
)

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_DECIMAL_RE = re.compile(r"^[0-9]+$")
_BYTES32_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_SIG_SCALAR_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


class PaymentError(Exception):
    """X-PAYMENT 解析/验签失败（code 进 402 质询）。"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class Authorization(BaseModel):
    """支付授权六元组（x402 同构）。value 为权威十进制字符串（最小单位）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", description="消费者钱包（须 == api key 绑定地址）")
    to: str = Field(description="收款方（V1 = PayVault 合约地址）")
    value: str = Field(description="金额，最小单位十进制字符串（须 == 服务定价）")
    valid_after: int = Field(alias="validAfter", ge=0, description="生效时间（unix 秒）")
    valid_before: int = Field(alias="validBefore", ge=0, description="失效时间（unix 秒）")
    nonce: str = Field(description="防重放随机数（bytes32 hex）")

    @field_validator("from_", "to")
    @classmethod
    def _address(cls, v: str) -> str:
        if not _ADDRESS_RE.match(v):
            raise ValueError(f"非法 EVM 地址: {v!r}")
        return v

    @field_validator("value")
    @classmethod
    def _value(cls, v: str) -> str:
        if not _DECIMAL_RE.match(v):
            raise ValueError(f"value 必须为十进制字符串: {v!r}")
        return v

    @field_validator("nonce")
    @classmethod
    def _nonce(cls, v: str) -> str:
        if not _BYTES32_RE.match(v):
            raise ValueError("nonce 必须为 0x 前缀 32 字节 hex")
        return v.lower()


class XPayment(Authorization):
    """Authorization + 签名三元组（X-PAYMENT 头的完整负载）。"""

    v: int = Field(description="恢复标识（27|28，兼容 0|1 自动归一）")
    r: str = Field(description="签名 r（0x + 32 字节 hex）")
    s: str = Field(description="签名 s（0x + 32 字节 hex）")

    @field_validator("v")
    @classmethod
    def _v(cls, v: int) -> int:
        if v in (0, 1):
            return v + 27
        if v in (27, 28):
            return v
        raise ValueError(f"v 必须为 0|1|27|28: {v}")

    @field_validator("r", "s")
    @classmethod
    def _scalar(cls, v: str) -> str:
        if not _SIG_SCALAR_RE.match(v):
            raise ValueError("r/s 必须为 0x 前缀 32 字节 hex")
        return v.lower()


def _pad_uint(n: int) -> bytes:
    return n.to_bytes(32, "big")


def _pad_address(addr: str) -> bytes:
    return int(addr, 16).to_bytes(32, "big")


def eip712_domain_separator(verifying_contract: str, chain_id: int) -> bytes:
    """EIP-712 domainSeparator（与 PayVault 合约逐字节一致，黄金向量锁定）。"""
    return keccak(
        DOMAIN_TYPEHASH
        + keccak(EIP712_DOMAIN_NAME.encode())
        + keccak(EIP712_DOMAIN_VERSION.encode())
        + _pad_uint(chain_id)
        + _pad_address(verifying_contract)
    )


def authorization_struct_hash(auth: Authorization) -> bytes:
    """Authorization 结构哈希（静态字段手工 abi.encode）。"""
    return keccak(
        AUTHORIZATION_TYPEHASH
        + _pad_address(auth.from_)
        + _pad_address(auth.to)
        + _pad_uint(int(auth.value))
        + _pad_uint(auth.valid_after)
        + _pad_uint(auth.valid_before)
        + bytes.fromhex(auth.nonce[2:])
    )


def eip712_digest(auth: Authorization, verifying_contract: str, chain_id: int) -> bytes:
    """\\x19\\x01 + domainSeparator + structHash（黄金向量：5ad07f3bb1efc103…）。"""
    return keccak(
        b"\x19\x01"
        + eip712_domain_separator(verifying_contract, chain_id)
        + authorization_struct_hash(auth)
    )


def recover_signer(payment: XPayment, verifying_contract: str, chain_id: int) -> str:
    """ecrecover(digest) → checksum 地址（本地验签，无网络）。"""
    digest = eip712_digest(payment, verifying_contract, chain_id)
    signature = eth_keys.Signature(vrs=(payment.v - 27, int(payment.r, 16), int(payment.s, 16)))
    public = signature.recover_public_key_from_msg_hash(digest)
    return to_checksum_address(public.to_address())


def build_x_payment_header(payment: XPayment) -> str:
    """X-PAYMENT 头 = base64(JSON{六元组 + v/r/s})（SDK 侧构造口径）。"""
    payload: dict[str, Any] = json.loads(payment.model_dump_json(by_alias=True))
    return base64.b64encode(json.dumps(payload).encode()).decode()


def parse_x_payment(header: str) -> XPayment:
    """解析并校验 X-PAYMENT 头；任何形态错误 → PaymentError(bad_xpayment)。"""
    try:
        raw = base64.b64decode(header, validate=True)
        payload = json.loads(raw)
        return XPayment.model_validate(payload)
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaymentError("bad_xpayment", f"X-PAYMENT 头非法: {exc}") from exc
    except PaymentError:
        raise
    except Exception as exc:  # pydantic ValidationError 等一切形态错误
        raise PaymentError("bad_xpayment", f"X-PAYMENT 头非法: {exc}") from exc
