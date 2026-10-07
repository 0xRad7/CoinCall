"""收据（02 §4 / 10 §2）：Ed25519 自验签 + HMAC 双签过渡。

- Ed25519（新，10 §2 冻结契约）：网关密钥对签名，规范串
  ``receipt_id|service_id|amount_raw|status|ts``（ts=unix 秒），任何持有公钥者
  （Agent/第三方/链下审计）可离线验证；公钥经 ``GET /internal/receipts/pubkey`` 发布。
- HMAC（旧，过渡窗口保留）：密钥在网关，仅本机可验；对完整收据 JSON 签名，
  响应头 ``X-Receipt-Sig`` 继续携带，旧消费方平滑迁移。

头部回执：X-Receipt-Id / X-Charged-Raw / X-Receipt-Sig / X-Receipt-Sig-Ed25519 / X-Receipt-Ts。
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

logger = logging.getLogger("coincall.gateway.receipt")

#: 私钥种子字节数（COINCALL_RECEIPT_SEED 为其 hex，64 个 hex 字符）
SEED_BYTES = 32


def build_receipt(
    *,
    call_id: str,
    service_id: str,
    provider_agent_id: int,
    consumer_key_id: str,
    amount_raw: str,
    result_hash: str,
) -> dict[str, Any]:
    return {
        "receipt_id": f"rcp_{uuid.uuid4().hex[:12]}",
        "call_id": call_id,
        "service_id": service_id,
        "provider_agent_id": provider_agent_id,
        "consumer_key_id": consumer_key_id,
        "amount_raw": amount_raw,
        "status": "success",
        "result_hash": result_hash,
        "created_at": datetime.now(UTC).isoformat(),
    }


def canonical_receipt(receipt: dict[str, Any]) -> str:
    return json.dumps(receipt, sort_keys=True, separators=(",", ":"))


def sign_receipt(receipt: dict[str, Any], secret: str) -> str:
    return hmac.new(
        secret.encode(), canonical_receipt(receipt).encode(), hashlib.sha256
    ).hexdigest()


def verify_receipt_sig(receipt: dict[str, Any], secret: str, signature: str) -> bool:
    return hmac.compare_digest(sign_receipt(receipt, secret), signature)


# ---- Ed25519（10 §2 冻结契约） ------------------------------------------------


def receipt_ts(receipt: dict[str, Any]) -> int:
    """收据时刻 → unix 秒（规范串的 ts；由 created_at 派生）。"""
    return int(datetime.fromisoformat(str(receipt["created_at"])).timestamp())


def canonical_receipt_string(
    *, receipt_id: str, service_id: str, amount_raw: str, status: str, ts: int
) -> str:
    """Ed25519 签名覆盖的规范串：``receipt_id|service_id|amount_raw|status|ts``。

    五元组全部是消费者可得的标量（响应头/路径），第三方无需网关参与即可重算。
    """
    return f"{receipt_id}|{service_id}|{amount_raw}|{status}|{ts}"


def receipt_canonical_of(receipt: dict[str, Any], ts: int | None = None) -> str:
    """收据 dict → 规范串（缺省 ts 由 created_at 派生）。"""
    return canonical_receipt_string(
        receipt_id=str(receipt["receipt_id"]),
        service_id=str(receipt["service_id"]),
        amount_raw=str(receipt["amount_raw"]),
        status=str(receipt["status"]),
        ts=receipt_ts(receipt) if ts is None else ts,
    )


class ReceiptSigner:
    """Ed25519 收据签名器：seed 可复现注入（env），缺省随机生成（日志提示）。"""

    def __init__(self, seed_hex: str | None = None) -> None:
        if seed_hex is None:
            self._key = Ed25519PrivateKey.generate()
            logger.warning(
                "COINCALL_RECEIPT_SEED 未注入：收据 Ed25519 密钥随机生成，"
                "重启轮换后旧收据将无法用 /internal/receipts/pubkey 验签（生产务必注入 seed）"
            )
            return
        try:
            seed = bytes.fromhex(seed_hex)
        except ValueError as exc:
            raise ValueError("COINCALL_RECEIPT_SEED 必须是 hex 字符串（32 字节）") from exc
        if len(seed) != SEED_BYTES:
            raise ValueError(f"COINCALL_RECEIPT_SEED 必须是 32 字节 hex（{SEED_BYTES * 2} 个字符）")
        self._key = Ed25519PrivateKey.from_private_bytes(seed)

    def public_key_hex(self) -> str:
        raw = self._key.public_key().public_bytes_raw()
        return raw.hex()

    def sign_receipt(self, receipt: dict[str, Any], ts: int | None = None) -> str:
        """收据 → Ed25519 签名（64 字节 hex），覆盖 receipt_canonical_of 规范串。"""
        return self._key.sign(receipt_canonical_of(receipt, ts).encode()).hex()


def verify_receipt_ed25519(
    receipt: dict[str, Any], public_key_hex: str, sig_hex: str, *, ts: int | None = None
) -> bool:
    """离线验签：公钥 hex + 收据（或五元组标量重建的等价 dict）→ bool。"""
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(bytes.fromhex(sig_hex), receipt_canonical_of(receipt, ts).encode())
    except (ValueError, InvalidSignature):
        return False
    return True
