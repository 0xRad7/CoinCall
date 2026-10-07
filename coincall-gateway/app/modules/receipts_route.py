"""收据公钥端点：GET /internal/receipts/pubkey（10 §2 冻结契约）。

Ed25519 公钥发布口径：core（反馈验签方）/任何第三方经 internal 通道读取公钥 hex，
即可对收据规范串 ``receipt_id|service_id|amount_raw|status|ts`` 离线验签。
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.modules.receipt import ReceiptSigner

router = APIRouter(prefix="/internal/receipts", tags=["receipts"])


class PubKeyView(BaseModel):
    public_key_hex: str


@router.get("/pubkey", response_model=PubKeyView)
def receipts_pubkey(request: Request) -> PubKeyView:
    signer: ReceiptSigner = request.app.state.receipt_signer
    return PubKeyView(public_key_hex=signer.public_key_hex())
