"""付费反馈权（10 §2 朴素版）：收据绑定 + 一次性核销。

反馈流：消费者持网关收据提交 → core Ed25519 验签（公钥自网关拉取）→
收据指向核验（service 匹配 / status=success）→ 防自评 → 窗口限频 →
nullifier 核销落库（一收据一反馈）。

防自评与限频的实施级边界（CONSTRAINTS §E，2026-10-06）：
- 冻结契约的收据规范串不含 payer，网关统计仅聚合口径（distinct_payers）——
  「payer==provider.wallet → 403」以 ReceiptPayerSource oracle 注入式落地，
  网关暴露逐收据 payer 前生产侧为 None（规则就绪、数据面待接）；
- 「同一钱包对同一服务每窗口 ≤1 条」在 payer 不可得时按聚合口径等价实施：
  同窗已受理反馈数 ≥ distinct_payers 即 429（每钱包至多贡献一条）。
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ApiError
from app.modules.receiptkey import receipt_canonical_message, verify_receipt_signature
from app.storage.db import CoreStore

router = APIRouter(tags=["feedback"])

#: 网关统计行的窗口口径（与决策默认窗一致；限频用同一窗口）
FEEDBACK_STATS_WINDOW_HOURS = 168


class ReceiptPayerSource(Protocol):
    """逐收据付款人源（防自评 403 的数据面；生产 None=规则挂起，见模块 docstring）。"""

    def payer_of(self, receipt_id: str) -> str | None: ...


class ReceiptClaim(BaseModel):
    """网关收据原样转呈：五元组为规范串成分（重建签名消息），sig 为 Ed25519 hex。"""

    model_config = ConfigDict(extra="forbid")

    receipt_id: str = Field(min_length=4, max_length=128)
    service_id: str = Field(min_length=1, max_length=128)
    amount_raw: str = Field(pattern=r"^[0-9]+$")
    status: str = Field(min_length=1, max_length=32)
    ts: str = Field(min_length=8, max_length=64)
    receipt_sig_hex: str = Field(min_length=64, max_length=256)


class FeedbackSubmitRequest(BaseModel):
    """POST /feedback：被评服务 + 收据转呈 + 评分。

    receipt.service_id（收据真实指向）≠ service_id（被评目标）→ 422 跨服务收据。
    """

    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(min_length=1, max_length=128)
    receipt: ReceiptClaim
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=280)


class FeedbackSubmitResponse(BaseModel):
    service_id: str
    receipt_id: str
    rating: int
    verified_paid: bool = Field(default=True, description="资格即收据：入库即已验证付费")
    created_at: str


class FeedbackEntryOut(BaseModel):
    receipt_id: str
    rating: int
    comment: str | None
    created_at: str


class FeedbackServiceResponse(BaseModel):
    service_id: str
    count: int
    avg: float | None
    verified_paid: bool = Field(default=True, description="恒 true：入库反馈资格即收据（10 §2）")
    entries: list[FeedbackEntryOut]


def _store(request: Request) -> CoreStore:
    return request.app.state.store


def _provider_wallet_of(svc: dict[str, Any]) -> str:
    provider = svc.get("manifest", {}).get("provider", {})
    return str(provider.get("wallet", "")).lower()


@router.post("/feedback", status_code=201, response_model=FeedbackSubmitResponse)
def submit_feedback(body: FeedbackSubmitRequest, request: Request) -> FeedbackSubmitResponse:
    """提交付费反馈（10 §2 反馈流）：验签 → 服务核验 → 防自评 → 限频 → 核销落库。"""
    pubkey: str | None = request.app.state.receipt_pubkey.public_key_hex()
    if pubkey is None:
        raise ApiError(
            status_code=503,
            error="receipt_key_unavailable",
            detail="网关收据公钥不可得（启动拉取与缓存均失败），反馈面暂不可用",
            code="receipt_key_unavailable",
        )
    message = receipt_canonical_message(
        body.receipt.receipt_id,
        body.receipt.service_id,
        body.receipt.amount_raw,
        body.receipt.status,
        body.receipt.ts,
    )
    if not verify_receipt_signature(pubkey, message, body.receipt.receipt_sig_hex):
        raise ApiError(
            status_code=401,
            error="unauthorized",
            detail="收据验签失败（Ed25519）",
            code="receipt_sig_invalid",
        )
    svc = _store(request).get_service(body.service_id)
    if svc is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"service 不存在: {body.service_id}",
            code="service_not_found",
        )
    if body.receipt.service_id != body.service_id:
        raise ApiError(
            status_code=422,
            error="invalid_request",
            detail=f"收据属于 {body.receipt.service_id}，与被评服务 {body.service_id} 不符",
            code="receipt_service_mismatch",
        )
    if body.receipt.status != "success":
        raise ApiError(
            status_code=422,
            error="invalid_request",
            detail=f"仅 success 收据可反馈（收据状态: {body.receipt.status}）",
            code="receipt_not_success",
        )
    _check_self_rating(request, body)
    _check_window_limit(request, body.service_id)
    if not _store(request).claim_feedback(
        body.receipt.receipt_id, body.service_id, body.rating, body.comment
    ):
        raise ApiError(
            status_code=409,
            error="conflict",
            detail=f"收据已核销（一收据一反馈）: {body.receipt.receipt_id}",
            code="receipt_redeemed",
        )
    created_at = next(
        (
            e["created_at"]
            for e in _store(request).list_feedback(body.service_id)
            if e["receipt_id"] == body.receipt.receipt_id
        ),
        "",
    )
    return FeedbackSubmitResponse(
        service_id=body.service_id,
        receipt_id=body.receipt.receipt_id,
        rating=body.rating,
        verified_paid=True,
        created_at=created_at,
    )


def _check_self_rating(request: Request, body: FeedbackSubmitRequest) -> None:
    """防自评：收据付款人==该服务 provider.wallet → 403（oracle 未接=规则挂起，§E）。"""
    oracle: ReceiptPayerSource | None = getattr(request.app.state, "feedback_payer", None)
    if oracle is None:
        return
    payer = oracle.payer_of(body.receipt.receipt_id)
    if payer is None:
        return
    svc = _store(request).get_service(body.service_id)
    if svc is None:  # 调用序保证不触发；防御早退（无 manifest 即无从比对钱包）
        return
    if payer.lower() == _provider_wallet_of(svc):
        raise ApiError(
            status_code=403,
            error="forbidden",
            detail="收据付款人即服务 provider 本人（防自评）",
            code="self_rating_blocked",
        )


def _check_window_limit(request: Request, service_id: str) -> None:
    """窗口限频（§E 聚合等价口径）：同窗已受理数 ≥ 网关 distinct_payers → 429。"""
    settings = request.app.state.app_settings
    window_hours: int = settings.feedback_window_hours
    gateway = request.app.state.gateway_stats
    try:
        stats = gateway.stats_view(FEEDBACK_STATS_WINDOW_HOURS)
    except Exception:
        return  # 网关不可达：限频数据面缺失时放行（收据核销+经济性护栏仍在）
    row = next(
        (r for r in stats.get("services", []) if str(r.get("service_id")) == service_id), None
    )
    if row is None or row.get("distinct_payers") is None:
        return
    cap = int(row["distinct_payers"])
    since = (datetime.now(UTC) - timedelta(hours=window_hours)).strftime("%Y-%m-%d %H:%M:%S")
    if _store(request).feedback_count_since(service_id, since) >= cap:
        raise ApiError(
            status_code=429,
            error="rate_limited",
            detail=(
                f"窗口 {window_hours}h 内该服务反馈已达去重支付者数上限 {cap}"
                "（同一钱包每服务每窗口至多 1 条，CONSTRAINTS §E）"
            ),
            code="feedback_window_limit",
        )


@router.get("/feedback/services/{service_id}", response_model=FeedbackServiceResponse)
def feedback_of_service(service_id: str, request: Request) -> FeedbackServiceResponse:
    """聚合 + 原始条目（每条含 receipt_id 与时间戳，可独立点验收据）。"""
    if _store(request).get_service(service_id) is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"service 不存在: {service_id}",
            code="service_not_found",
        )
    entries = _store(request).list_feedback(service_id)
    ratings = [int(e["rating"]) for e in entries]
    avg = round(sum(ratings) / len(ratings), 4) if ratings else None
    return FeedbackServiceResponse(
        service_id=service_id,
        count=len(entries),
        avg=avg,
        verified_paid=True,
        entries=[FeedbackEntryOut(**e) for e in entries],
    )
