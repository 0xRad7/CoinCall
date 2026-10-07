"""网关收据公钥拉取与 Ed25519 验签（10 §2 前置升级，core 消费侧）。

契约（与 gateway 任务对齐冻结）：
- 网关 `GET /internal/receipts/pubkey` → `{"public_key_hex"}`（Ed25519，32 字节裸 hex）；
- 签名消息=规范串 `receipt_id|service_id|amount_raw|status|ts`，双侧同串；
- 公钥启动时拉取 + TTL 缓存（默认 5min）；拉取失败 → 反馈接口 503 降级，不影响其他面。
"""

import time
from typing import Protocol

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

#: Ed25519 公钥裸长度（字节）
ED25519_KEY_LEN = 32


class ReceiptPubkeySource(Protocol):
    """收据公钥源协议：返回 hex 或 None（当前不可用，反馈面降级 503）。"""

    def public_key_hex(self) -> str | None: ...


class ReceiptPubkeyClient:
    """真实公钥源：8030 /internal/receipts/pubkey，TTL 缓存 + 过期缓存兜底。"""

    def __init__(self, http: httpx.Client, base_url: str, ttl: float = 300.0) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._ttl = ttl
        self._key: str | None = None
        self._fetched_at: float = 0.0

    def public_key_hex(self) -> str | None:
        now = time.monotonic()
        if self._key is not None and now - self._fetched_at < self._ttl:
            return self._key
        try:
            resp = self._http.get(f"{self._base}/internal/receipts/pubkey")
            resp.raise_for_status()
            hexkey = str(resp.json()["public_key_hex"]).strip().lower()
            if len(bytes.fromhex(hexkey)) != ED25519_KEY_LEN:
                raise ValueError("public_key_hex 非 32 字节")
        except Exception:  # 拉取失败：过期缓存兜底（有则用），否则 None（503 语义）
            if self._key is not None:
                return self._key
            return None
        self._key = hexkey
        self._fetched_at = now
        return self._key


def receipt_canonical_message(
    receipt_id: str, service_id: str, amount_raw: str, status: str, ts: str
) -> str:
    """冻结契约规范串（网关签名侧同串定义）：receipt_id|service_id|amount_raw|status|ts。"""
    return f"{receipt_id}|{service_id}|{amount_raw}|{status}|{ts}"


def verify_receipt_signature(public_key_hex: str, message: str, sig_hex: str) -> bool:
    """Ed25519 验签（第三方可离线复算同一函数）；形态不符/验签失败一律 False。"""
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        signature = bytes.fromhex(sig_hex)
    except (ValueError, TypeError):
        return False
    try:
        key.verify(signature, message.encode())
    except InvalidSignature:
        return False
    return True
