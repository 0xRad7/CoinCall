"""收据（02 §4）：HMAC 防伪 + 头部回执（X-Receipt-Id / X-Charged-Raw / X-Receipt-Sig）。"""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any


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
