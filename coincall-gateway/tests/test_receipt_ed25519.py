"""unit（10 §2 冻结契约）：收据 Ed25519 自验签 + HMAC 双签过渡。

契约口径：
- 规范串 `receipt_id|service_id|amount_raw|status|ts`（ts=unix 秒）；
- 响应头 `X-Receipt-Sig-Ed25519`（64 字节 hex=128 字符），`X-Receipt-Sig`（HMAC）保留双签；
- `GET /internal/receipts/pubkey` → {"public_key_hex"}——任何持有公钥者可离线验签；
- `COINCALL_RECEIPT_SEED` 可复现注入（32 字节 hex），缺省随机生成。
"""

import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.modules.receipt import (
    ReceiptSigner,
    build_receipt,
    canonical_receipt_string,
    receipt_ts,
    sign_receipt,
    verify_receipt_ed25519,
    verify_receipt_sig,
)
from tests.conftest import call_headers, gateway_serve

pytestmark = pytest.mark.unit

#: RFC 8032 Ed25519 测试向量种子（32 字节）
SEED_HEX = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"


def _sample_receipt() -> dict[str, object]:
    return build_receipt(
        call_id="call_abc123",
        service_id="svc_translate_v1",
        provider_agent_id=137,
        consumer_key_id="key_unit1",
        amount_raw="10000",
        result_hash="sha256:" + "0" * 64,
    )


def test_canonical_receipt_string_format() -> None:
    receipt = _sample_receipt()
    ts = 1_800_000_000
    canonical = canonical_receipt_string(
        receipt_id=str(receipt["receipt_id"]),
        service_id="svc_translate_v1",
        amount_raw="10000",
        status="success",
        ts=ts,
    )
    assert canonical == f"{receipt['receipt_id']}|svc_translate_v1|10000|success|{ts}"


def test_receipt_ts_derives_from_created_at() -> None:
    receipt = _sample_receipt()
    assert abs(receipt_ts(receipt) - time.time()) < 5  # unix 秒，建据时刻


def test_signer_seed_reproducible_and_verify_roundtrip() -> None:
    """同种子 → 同公钥；签名 → 公钥裸验（第三方离线口径，不碰私钥）。"""
    a = ReceiptSigner(seed_hex=SEED_HEX)
    b = ReceiptSigner(seed_hex=SEED_HEX)
    assert a.public_key_hex() == b.public_key_hex()

    receipt = _sample_receipt()
    sig_hex = a.sign_receipt(receipt)
    assert len(sig_hex) == 128  # 64 字节 hex

    # 离线验签：只用公钥 hex + 五元组标量（消费者可得的全部信息）
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(a.public_key_hex()))
    canonical = canonical_receipt_string(
        receipt_id=str(receipt["receipt_id"]),
        service_id="svc_translate_v1",
        amount_raw="10000",
        status="success",
        ts=receipt_ts(receipt),
    )
    pub.verify(bytes.fromhex(sig_hex), canonical.encode())

    assert verify_receipt_ed25519(receipt, a.public_key_hex(), sig_hex)
    # 篡改任何字段都验不过（ts 偏移 / amount 改写）
    wrong_ts = receipt_ts(receipt) + 1
    assert not verify_receipt_ed25519(receipt, a.public_key_hex(), sig_hex, ts=wrong_ts)
    tampered = dict(receipt)
    tampered["amount_raw"] = "999999"
    assert not verify_receipt_ed25519(tampered, a.public_key_hex(), sig_hex)


def test_signer_random_generate_diverges() -> None:
    a = ReceiptSigner()  # 缺省随机（生产应注入 seed；本用例只验证密钥对独立可用）
    receipt = _sample_receipt()
    assert verify_receipt_ed25519(receipt, a.public_key_hex(), a.sign_receipt(receipt))
    assert ReceiptSigner().public_key_hex() != a.public_key_hex()


def test_signer_rejects_bad_seed() -> None:
    with pytest.raises(ValueError, match="32 字节"):
        ReceiptSigner(seed_hex="nothex")
    with pytest.raises(ValueError, match="32 字节"):
        ReceiptSigner(seed_hex="ab" * 31)


def test_hmac_legacy_still_roundtrip() -> None:
    """双签过渡窗口：HMAC 旧口径对完整收据 JSON 仍可验（网关本机验证）。"""
    receipt = _sample_receipt()
    sig = sign_receipt(receipt, "coincall-dev-receipt-secret")
    assert verify_receipt_sig(receipt, "coincall-dev-receipt-secret", sig)
    assert not verify_receipt_sig(receipt, "other-secret", sig)


async def test_call_response_dual_signed(settings: object) -> None:  # type: ignore[arg-type]
    """付费调用回执双签并存：HMAC 旧头 + Ed25519 新头 + ts 头；公钥端点可离线验。"""
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1", json={"text": "hi"}, headers=call_headers()
        )
        assert resp.status_code == 200, resp.text
        receipt_id = resp.headers["X-Receipt-Id"]
        ts = int(resp.headers["X-Receipt-Ts"])
        hmac_sig = resp.headers["X-Receipt-Sig"]
        ed_sig = resp.headers["X-Receipt-Sig-Ed25519"]
        assert len(hmac_sig) == 64  # HMAC-SHA256 hex（旧头保留，双签过渡窗口）
        assert len(ed_sig) == 128  # Ed25519 64 字节 hex

        pub_resp = await client.get("/internal/receipts/pubkey")
        assert pub_resp.status_code == 200, pub_resp.text
        public_key_hex = pub_resp.json()["public_key_hex"]

        # 第三方离线验签：五元组全部来自响应头/路径，无需网关参与
        canonical = canonical_receipt_string(
            receipt_id=receipt_id,
            service_id="svc_translate_v1",
            amount_raw="10000",
            status="success",
            ts=ts,
        )
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(bytes.fromhex(ed_sig), canonical.encode())


async def test_pubkey_endpoint_reflects_seed(settings: object) -> None:  # type: ignore[arg-type]
    settings.receipt_seed = SEED_HEX  # type: ignore[attr-defined]
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        body = (await client.get("/internal/receipts/pubkey")).json()
        assert body["public_key_hex"] == ReceiptSigner(seed_hex=SEED_HEX).public_key_hex()
