"""决策层 API（10 篇）：带证明的四分量排序——收入(链上)×履约(网关)×反馈(付费)×新鲜度。

评分公式（§0.5 本轮裁决，全部在响应里自描述，Agent 可独立重算）：
- score = 0.4*revenue + 0.25*fulfillment + 0.2*feedback + 0.15*freshness
- revenue     = 0.5*norm(ln(total_raw+1)) + 0.5*norm(distinct_payers)
                （total_raw=链上 Charged 全量真相，复用 charged_events；distinct=网关窗口统计）
- fulfillment = success_rate * latency_bonus(p95_ms)（≤2s 满分、≥10s 零分、其间线性；
                success_rate=(success+settled)/(success+settled+aborted)，坏账不入分母）
- feedback    = norm(bayesian_avg)（先验=全局均值，先验强度 m=10；低样本向先验收缩）
- freshness   = exp(-ln2*Δh/48)（Δh=as_of−last_activity；as_of 显式传入即演示时间注入）
- norm = 窗口内 min-max（候选集=本次响应的分区内服务；零区间=0.5）
"""

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ApiError
from app.modules.leaderboard import (
    GatewayStatsSource,
    _sync_quietly,
    to_amount,
)
from app.modules.manifest import CATEGORIES, manifest_category, manifest_tags
from app.storage.db import CoreStore

router = APIRouter(tags=["decision"])

#: 冻结权重（10 §0.5）；响应自描述，改动=契约级变更
DECISION_WEIGHTS: dict[str, float] = {
    "revenue": 0.4,
    "fulfillment": 0.25,
    "feedback": 0.2,
    "freshness": 0.15,
}
SCORE_FORMULA = (
    "score = 0.4*revenue + 0.25*fulfillment + 0.2*feedback + 0.15*freshness；"
    "revenue=0.5*norm(ln(total_raw+1))+0.5*norm(distinct_payers)；"
    "fulfillment=success_rate*latency_bonus(p95)（≤2000ms=1，≥10000ms=0，线性）；"
    "feedback=norm(bayesian_avg)（m=10，先验=全局均值）；"
    "freshness=exp(-ln2*Δh/48)"
)
NORM_NOTE = "norm=窗口内 min-max（候选集=响应分区内的服务；零区间=0.5）"

FRESHNESS_HALF_LIFE_H = 48.0
FEEDBACK_PRIOR_STRENGTH = 10
FEEDBACK_PRIOR_NEUTRAL = 3.0
DEFAULT_WINDOW_HOURS = 168
LATENCY_BONUS_FULL_MS = 2_000
LATENCY_BONUS_ZERO_MS = 10_000
#: 交易哈希 0x + 64 hex
TX_HASH_HEX_LEN = 66
REVENUE_FORMULA = "0.5*norm(ln(total_raw+1)) + 0.5*norm(distinct_payers)"
FULFILLMENT_FORMULA = (
    "success_rate * latency_bonus(p95_ms)；success_rate=(success+settled)/"
    "(success+settled+aborted)；bonus: p95≤2000ms=1，≥10000ms=0，其间线性"
)
FEEDBACK_FORMULA = (
    "norm(bayesian_avg)；bayesian_avg=(m*prior+n*avg)/(m+n)，m=10，先验=全局均值（空库=3.0）"
)
FRESHNESS_FORMULA = "exp(-ln2*Δh/48)"


# ---------------------------------------------------------------------------
# 纯函数分量（单测口径=本模块 docstring 公式）
# ---------------------------------------------------------------------------


def parse_ts(value: object) -> datetime | None:
    """网关/入参时间戳解析：ISO 或 'YYYY-MM-DD HH:MM:SS'；naive 视作 UTC；不可解析 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def parse_as_of(value: str) -> datetime:
    """as_of 解析（默认 now）；非法 → 422 bad_as_of（演示时间注入参数）。"""
    if not value.strip():
        return datetime.now(UTC)
    dt = parse_ts(value)
    if dt is None:
        raise ApiError(
            status_code=422,
            error="invalid_request",
            detail=f"as_of 非法 ISO 时间: {value!r}",
            code="bad_as_of",
        )
    return dt


def latency_bonus(p95_ms: int | None) -> float | None:
    """p95 → 延迟加分：≤2s=1，≥10s=0，其间线性；无数据 None（不折算为 0 值证据）。"""
    if p95_ms is None:
        return None
    if p95_ms <= LATENCY_BONUS_FULL_MS:
        return 1.0
    if p95_ms >= LATENCY_BONUS_ZERO_MS:
        return 0.0
    return (LATENCY_BONUS_ZERO_MS - p95_ms) / (LATENCY_BONUS_ZERO_MS - LATENCY_BONUS_FULL_MS)


def bayesian_avg(count: int, avg: float | None, prior: float, m: int) -> float:
    """贝叶斯收缩均值：低样本向先验收缩（10 §3，防 3 票满分压 300 票）。"""
    if count <= 0 or avg is None:
        return prior
    return (m * prior + count * avg) / (m + count)


def freshness_score(last_activity: datetime | None, as_of: datetime) -> tuple[float, float | None]:
    """(score, age_h)：无活动=(0.0, None)；Δh 负值钳 0（as_of 早于活动不加分）。"""
    if last_activity is None:
        return (0.0, None)
    age_h = max(0.0, (as_of - last_activity).total_seconds() / 3600)
    return (math.pow(2, -age_h / FRESHNESS_HALF_LIFE_H), age_h)


def _norm(values: list[float]) -> list[float]:
    """窗口内 min-max；零区间 → 0.5（全员等值时中性，不影响相对排序）。"""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _to_int(value: Any) -> int | None:  # noqa: ANN401 —— 网关统计行字段形态未冻结到类型
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:  # noqa: ANN401 —— 同 _to_int：宽松入参窄化出口
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 取数上下文（一次收集，多端点共用）
# ---------------------------------------------------------------------------


@dataclass
class _Context:
    """决策取数快照：active 服务 + 网关窗口统计 + 链上收入 + 反馈聚合 + 锚定。"""

    services: list[dict[str, Any]]
    stats_by_service: dict[str, dict[str, Any]]
    stats_available: bool
    revenue_by_wallet: dict[str, dict[str, Any]]
    fb_by_service: dict[str, dict[str, Any]]
    prior: float
    anchor_latest: dict[int, dict[str, Any]]
    payload_by_agent: dict[int, dict[str, Any]]
    digest_by_agent: dict[int, str]
    window_hours: int
    degraded: list[str] = field(default_factory=list)


def _fulfillment_metrics(calls: dict[str, Any] | None) -> dict[str, Any]:
    """网关统计行 → 履约指标（口径：docstring 公式；calls 缺失=不可用）。"""
    if calls is None:
        return {
            "available": False,
            "calls_success": None,
            "calls_settled": None,
            "calls_aborted": None,
            "bad_debt": None,
            "success_rate": None,
            "p50_ms": None,
            "p95_ms": None,
        }
    success = _to_int(calls.get("calls_success"))
    settled = _to_int(calls.get("calls_settled"))
    aborted = _to_int(calls.get("calls_aborted"))
    num = (success or 0) + (settled or 0)
    den = num + (aborted or 0)
    rate = round(num / den, 6) if den > 0 else None
    return {
        "available": True,
        "calls_success": success,
        "calls_settled": settled,
        "calls_aborted": aborted,
        "bad_debt": _to_int(calls.get("bad_debt")),
        "success_rate": rate,
        "p50_ms": _to_int(calls.get("p50_ms")),
        "p95_ms": _to_int(calls.get("p95_ms")),
    }


def _canonical_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _build_context(request: Request, *, window_hours: int) -> _Context:
    store: CoreStore = request.app.state.store
    _sync_quietly(request)  # Charged 收入懒同步（锁+最小间隔，同排行榜通道）
    services = store.list_services("active")
    stats: dict[str, Any] | None = None
    degraded: list[str] = []
    gateway: GatewayStatsSource = request.app.state.gateway_stats
    try:
        stats = gateway.stats_view(window_hours)
    except Exception as exc:  # 网关不可达：决策面降级（履约/新鲜度按无数据），不 500
        degraded.append(f"gateway_stats_failed: {exc.__class__.__name__}")
    stats_by_service: dict[str, dict[str, Any]] = {
        str(row.get("service_id")): row for row in (stats or {}).get("services", [])
    }
    revenue_by_wallet = {r["provider"]: r for r in store.charged_by_provider()}
    fb_by_service = store.feedback_by_service()
    fb_global = store.feedback_global()
    prior = float(fb_global["avg"]) if fb_global["avg"] is not None else FEEDBACK_PRIOR_NEUTRAL
    anchor_latest = store.anchor_latest_map()

    # 锚定载荷（10 §1：fulfillment+feedback 合并 JSON 的 sha256）按 provider 分组；
    # payload 与 digest 同源同构（explain/pending 透出的载荷与被签名摘要恒一致）
    by_agent: dict[int, dict[str, dict[str, Any]]] = {}
    for svc in services:
        provider = svc["manifest"].get("provider", {})
        agent_id = provider.get("agent_id")
        if agent_id is None:
            continue
        sid = svc["service_id"]
        metrics = _fulfillment_metrics(stats_by_service.get(sid))
        fb = fb_by_service.get(sid, {"count": 0, "avg": None})
        by_agent.setdefault(int(agent_id), {})[sid] = {
            "fulfillment": {**metrics, "window_hours": window_hours},
            "feedback": fb,
        }
    payload_by_agent: dict[int, dict[str, Any]] = {}
    digest_by_agent: dict[int, str] = {}
    for agent_id, services_payload in by_agent.items():
        payload = {
            "version": 1,
            "provider_agent_id": agent_id,
            "window_hours": window_hours,
            "services": services_payload,
        }
        payload_by_agent[agent_id] = payload
        digest_by_agent[agent_id] = _canonical_digest(payload)
    return _Context(
        services=services,
        stats_by_service=stats_by_service,
        stats_available=stats is not None,
        revenue_by_wallet=revenue_by_wallet,
        fb_by_service=fb_by_service,
        prior=prior,
        anchor_latest=anchor_latest,
        payload_by_agent=payload_by_agent,
        digest_by_agent=digest_by_agent,
        window_hours=window_hours,
        degraded=degraded,
    )


# ---------------------------------------------------------------------------
# 视图模型
# ---------------------------------------------------------------------------


class RevenueComponent(BaseModel):
    total_raw: int = Field(description="链上 Charged 聚合（唯一真相，全量无时间窗）")
    total: str
    charged_count: int
    distinct_payers: int | None = Field(description="网关窗口去重支付钱包数（§0.5 新增指标）")
    score_component: float
    formula: str = REVENUE_FORMULA
    proof: str = Field(description="链上收入证明端点（ Charged 交易哈希清单）")


class FulfillmentComponent(BaseModel):
    available: bool
    calls_success: int | None
    calls_settled: int | None
    calls_aborted: int | None
    bad_debt: int | None
    success_rate: float | None
    p50_ms: int | None
    p95_ms: int | None
    window_hours: int
    score_component: float | None = Field(
        default=None, description="无履约数据时 None（总分按 0 计，证据不伪造零值）"
    )
    formula: str = FULFILLMENT_FORMULA
    proof: dict[str, str | None] = Field(
        description="digest=履约+反馈聚合 JSON sha256；anchor_tx=已锚定交易（可核）"
    )


class FeedbackComponent(BaseModel):
    count: int
    avg: float | None
    bayesian_avg: float
    prior: float
    prior_strength: int = FEEDBACK_PRIOR_STRENGTH
    verified_paid: bool = Field(
        default=True, description="资格即收据：入库反馈恒为已验证付费（10 §2）"
    )
    score_component: float
    formula: str = FEEDBACK_FORMULA
    proof: str


class FreshnessComponent(BaseModel):
    last_activity_at: str | None
    age_h: float | None
    half_life_h: float = FRESHNESS_HALF_LIFE_H
    score_component: float
    formula: str = FRESHNESS_FORMULA


class Components(BaseModel):
    revenue: RevenueComponent
    fulfillment: FulfillmentComponent
    feedback: FeedbackComponent
    freshness: FreshnessComponent


class DecisionRow(BaseModel):
    service_id: str
    name: str
    status: str
    category: str
    tags: list[str]
    provider_wallet: str
    provider_agent_id: int | None
    price_raw: str = Field(description="manifest pricing.amount_raw（权威值）")
    price: str
    score: float
    components: Components


class DecisionServicesResponse(BaseModel):
    category: str | None
    window_hours: int
    as_of: str = Field(description="本次评分基准时刻（显式传入=演示时间注入）")
    sort: str
    weights: dict[str, float]
    formula: str
    norm: str
    services: list[DecisionRow]
    degraded: list[str]


class DecisionCategoriesResponse(BaseModel):
    categories: list[str]
    counts: dict[str, int]
    total_active: int


class FeedbackEntry(BaseModel):
    receipt_id: str
    rating: int
    comment: str | None
    created_at: str


class AnchorInfo(BaseModel):
    digest: str
    anchor_tx: str | None
    anchored_at: str | None
    payload: dict[str, Any]


class ExplainResponse(DecisionRow):
    window_hours: int
    as_of: str
    weights: dict[str, float]
    feedback_entries: list[FeedbackEntry] = Field(description="反馈原始条目（receipt 可点验）")
    anchor: AnchorInfo


class AnchorPendingItem(BaseModel):
    provider_agent_id: int
    digest: str
    payload: dict[str, Any]


class AnchorPendingResponse(BaseModel):
    window_hours: int
    pending: list[AnchorPendingItem]


class AnchorResultRequest(BaseModel):
    """keeper 锚定回执：{agent_id, digest, tx_hash}（幂等落 anchor_records）。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: int = Field(ge=1)
    digest: str
    tx_hash: str


class AnchorResultResponse(BaseModel):
    agent_id: int
    digest: str
    tx_hash: str
    duplicate: bool


# ---------------------------------------------------------------------------
# 行计算（归一化在候选集内）
# ---------------------------------------------------------------------------


def _candidate_rows(ctx: _Context, category: str | None, as_of: datetime) -> list[dict[str, Any]]:
    """分区过滤 + 逐行原始值收集（归一化输入）。"""
    rows: list[dict[str, Any]] = []
    for svc in ctx.services:
        manifest = svc["manifest"]
        cat = manifest_category(manifest)
        if category is not None and cat != category:
            continue
        provider = manifest.get("provider", {})
        wallet = str(provider.get("wallet", "")).lower()
        agent_id = provider.get("agent_id")
        revenue = ctx.revenue_by_wallet.get(wallet)
        total_raw = int(revenue["revenue_raw"]) if revenue else 0
        calls = ctx.stats_by_service.get(svc["service_id"])
        distinct = _to_int(calls.get("distinct_payers")) if calls else None
        last_activity = (
            parse_ts(calls.get("last_activity_at") or calls.get("last_call_at")) if calls else None
        )
        fb = ctx.fb_by_service.get(svc["service_id"], {"count": 0, "avg": None})
        fresh, age_h = freshness_score(last_activity, as_of)
        pricing = manifest.get("pricing", {})
        rows.append(
            {
                "service_id": svc["service_id"],
                "name": str(manifest.get("name", "")),
                "status": svc["status"],
                "category": cat,
                "tags": manifest_tags(manifest),
                "provider_wallet": wallet,
                "provider_agent_id": int(agent_id) if agent_id is not None else None,
                "price_raw": str(pricing.get("amount_raw", "0")),
                "price": to_amount(int(pricing.get("amount_raw", "0"))),
                "total_raw": total_raw,
                "charged_count": int(revenue["charged_count"]) if revenue else 0,
                "distinct_payers": distinct,
                "fulfillment": _fulfillment_metrics(calls),
                "fb_count": int(fb["count"]),
                "fb_avg": fb["avg"],
                "bayesian": bayesian_avg(
                    int(fb["count"]), fb["avg"], ctx.prior, FEEDBACK_PRIOR_STRENGTH
                ),
                "freshness": fresh,
                "last_activity_at": last_activity.isoformat() if last_activity else None,
                "age_h": age_h,
            }
        )
    return rows


def _finalize_rows(ctx: _Context, rows: list[dict[str, Any]]) -> list[DecisionRow]:
    """候选集内 min-max 归一化 + 加权合成（公式=模块 docstring，响应自描述）。"""
    norm_ln = _norm([math.log(r["total_raw"] + 1) for r in rows])
    norm_distinct = _norm([float(r["distinct_payers"] or 0) for r in rows])
    norm_bayes = _norm([r["bayesian"] for r in rows])
    out: list[DecisionRow] = []
    for i, r in enumerate(rows):
        rev_value = 0.5 * norm_ln[i] + 0.5 * norm_distinct[i]
        metrics: dict[str, Any] = r["fulfillment"]
        bonus = latency_bonus(metrics["p95_ms"])
        ful_value = (
            None
            if metrics["success_rate"] is None or bonus is None
            else metrics["success_rate"] * bonus
        )
        fb_value = norm_bayes[i]
        fresh_value = r["freshness"]
        score = (
            DECISION_WEIGHTS["revenue"] * rev_value
            + DECISION_WEIGHTS["fulfillment"] * (ful_value or 0.0)
            + DECISION_WEIGHTS["feedback"] * fb_value
            + DECISION_WEIGHTS["freshness"] * fresh_value
        )
        agent_id = r["provider_agent_id"]
        digest = ctx.digest_by_agent.get(agent_id) if agent_id is not None else None
        anchor = ctx.anchor_latest.get(agent_id) if agent_id is not None else None
        out.append(
            DecisionRow(
                service_id=r["service_id"],
                name=r["name"],
                status=r["status"],
                category=r["category"],
                tags=r["tags"],
                provider_wallet=r["provider_wallet"],
                provider_agent_id=agent_id,
                price_raw=r["price_raw"],
                price=r["price"],
                score=round(score, 4),
                components=Components(
                    revenue=RevenueComponent(
                        total_raw=r["total_raw"],
                        total=to_amount(r["total_raw"]),
                        charged_count=r["charged_count"],
                        distinct_payers=r["distinct_payers"],
                        score_component=round(rev_value, 4),
                        proof=(
                            f"/leaderboard/providers/{r['provider_wallet']}/proof"
                            if r["provider_wallet"]
                            else ""
                        ),
                    ),
                    fulfillment=FulfillmentComponent(
                        available=metrics["available"],
                        calls_success=metrics["calls_success"],
                        calls_settled=metrics["calls_settled"],
                        calls_aborted=metrics["calls_aborted"],
                        bad_debt=metrics["bad_debt"],
                        success_rate=metrics["success_rate"],
                        p50_ms=metrics["p50_ms"],
                        p95_ms=metrics["p95_ms"],
                        window_hours=ctx.window_hours,
                        score_component=None if ful_value is None else round(ful_value, 4),
                        proof={
                            "digest": digest or "",
                            "anchor_tx": anchor["tx_hash"] if anchor else None,
                        },
                    ),
                    feedback=FeedbackComponent(
                        count=r["fb_count"],
                        avg=None if r["fb_avg"] is None else round(r["fb_avg"], 4),
                        bayesian_avg=round(r["bayesian"], 4),
                        prior=round(ctx.prior, 4),
                        score_component=round(fb_value, 4),
                        proof=f"/feedback/services/{r['service_id']}",
                    ),
                    freshness=FreshnessComponent(
                        last_activity_at=r["last_activity_at"],
                        age_h=None if r["age_h"] is None else round(r["age_h"], 3),
                        score_component=round(fresh_value, 4),
                    ),
                ),
            )
        )
    return out


def _sort_rows(rows: list[DecisionRow], sort: str) -> list[DecisionRow]:
    if sort == "price":
        return sorted(rows, key=lambda r: (-int(r.price_raw), r.service_id))
    return sorted(rows, key=lambda r: (-r.score, r.service_id))


def _check_category(category: str) -> str | None:
    """空=全量分区；非法词表值 422。"""
    if not category:
        return None
    if category not in CATEGORIES:
        raise ApiError(
            status_code=422,
            error="invalid_request",
            detail=f"未知 category: {category!r}（词表: {CATEGORIES}）",
            code="unknown_category",
        )
    return category


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/decision/categories", response_model=DecisionCategoriesResponse)
def decision_categories(request: Request) -> DecisionCategoriesResponse:
    """类目词表 + 各计数（按 active manifests；缺省 other，10 §0.5）。"""
    store: CoreStore = request.app.state.store
    counts = dict.fromkeys(CATEGORIES, 0)
    active = store.list_services("active")
    for svc in active:
        counts[manifest_category(svc["manifest"])] += 1
    return DecisionCategoriesResponse(
        categories=list(CATEGORIES), counts=counts, total_active=len(active)
    )


@router.get("/decision/services", response_model=DecisionServicesResponse)
def decision_services(
    request: Request,
    category: str = "",
    window_hours: int = Query(DEFAULT_WINDOW_HOURS, ge=1, le=87_600),
    as_of: str = "",
    sort: str = "score",
) -> DecisionServicesResponse:
    """带证明的排序（10 §3）：分区内四分量加权 + 每行证据包（as_of=演示时间注入）。"""
    if sort not in ("score", "price"):
        raise ApiError(
            status_code=422,
            error="invalid_request",
            detail=f"sort 仅支持 score|price: {sort!r}",
            code="bad_sort",
        )
    partition = _check_category(category)
    as_of_dt = parse_as_of(as_of)
    ctx = _build_context(request, window_hours=window_hours)
    rows = _candidate_rows(ctx, partition, as_of_dt)
    final = _sort_rows(_finalize_rows(ctx, rows), sort)
    return DecisionServicesResponse(
        category=partition,
        window_hours=window_hours,
        as_of=as_of_dt.isoformat(),
        sort=sort,
        weights=DECISION_WEIGHTS,
        formula=SCORE_FORMULA,
        norm=NORM_NOTE,
        services=final,
        degraded=ctx.degraded,
    )


@router.get("/decision/explain/{service_id}", response_model=ExplainResponse)
def decision_explain(
    service_id: str,
    request: Request,
    window_hours: int = Query(DEFAULT_WINDOW_HOURS, ge=1, le=87_600),
    as_of: str = "",
) -> ExplainResponse:
    """单服务完整证据包（10 §3）：含反馈原始条目（receipt_id+时间戳）与锚定载荷。"""
    as_of_dt = parse_as_of(as_of)
    ctx = _build_context(request, window_hours=window_hours)
    target = next((s for s in ctx.services if s["service_id"] == service_id), None)
    if target is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"service 不存在或非 active: {service_id}",
            code="service_not_found",
        )
    # 归一化基准=该服务所在类目分区（与 /decision/services?category= 同口径）
    partition = manifest_category(target["manifest"])
    rows = _candidate_rows(ctx, partition, as_of_dt)
    finalized = _finalize_rows(ctx, rows)
    row = next(r for r in finalized if r.service_id == service_id)
    store: CoreStore = request.app.state.store
    entries = [
        FeedbackEntry(
            receipt_id=e["receipt_id"],
            rating=int(e["rating"]),
            comment=e["comment"],
            created_at=e["created_at"],
        )
        for e in store.list_feedback(service_id)
    ]
    agent_id = row.provider_agent_id
    digest = ctx.digest_by_agent.get(agent_id) if agent_id is not None else None
    anchor = ctx.anchor_latest.get(agent_id) if agent_id is not None else None
    return ExplainResponse(
        **row.model_dump(),
        window_hours=window_hours,
        as_of=as_of_dt.isoformat(),
        weights=DECISION_WEIGHTS,
        feedback_entries=entries,
        anchor=AnchorInfo(
            digest=digest or "",
            anchor_tx=anchor["tx_hash"] if anchor else None,
            anchored_at=anchor["anchored_at"] if anchor else None,
            payload=_anchor_payload_for(ctx, agent_id),
        ),
    )


def _anchor_payload_for(ctx: _Context, agent_id: int | None) -> dict[str, Any]:
    """该 agent 的锚定载荷（与 digest 同源：取自 _Context 构建期的同一对象）。"""
    if agent_id is None:
        return {}
    return ctx.payload_by_agent.get(agent_id, {})


@router.get("/internal/decision/anchor-pending", response_model=AnchorPendingResponse)
def anchor_pending(
    request: Request,
    window_hours: int = Query(DEFAULT_WINDOW_HOURS, ge=1, le=87_600),
) -> AnchorPendingResponse:
    """待锚定清单：digest 与最新锚定不一致（或从未锚定）的 provider（keeper 拉取）。"""
    ctx = _build_context(request, window_hours=window_hours)
    items: list[AnchorPendingItem] = []
    for agent_id, digest in sorted(ctx.digest_by_agent.items()):
        if ctx.anchor_latest.get(agent_id, {}).get("digest") == digest:
            continue
        items.append(
            AnchorPendingItem(
                provider_agent_id=agent_id,
                digest=digest,
                payload=_anchor_payload_for(ctx, agent_id),
            )
        )
    return AnchorPendingResponse(window_hours=window_hours, pending=items)


@router.post("/internal/decision/anchor-result", response_model=AnchorResultResponse)
def anchor_result(body: AnchorResultRequest, request: Request) -> AnchorResultResponse:
    """锚定回执落库（幂等）：digest 必须命中当前 pending 计算，防锚定过期摘要。"""
    ctx = _build_context(request, window_hours=DEFAULT_WINDOW_HOURS)
    if body.agent_id not in ctx.digest_by_agent:
        raise ApiError(
            status_code=422,
            error="invalid_request",
            detail=f"agent 无 active 服务或未知: {body.agent_id}",
            code="anchor_agent_unknown",
        )
    if body.digest != ctx.digest_by_agent[body.agent_id]:
        raise ApiError(
            status_code=422,
            error="invalid_request",
            detail="digest 与当前待锚定摘要不一致（过期/算错）",
            code="anchor_digest_mismatch",
        )
    tx = body.tx_hash.strip().lower()
    if not tx.startswith("0x") or len(tx) != TX_HASH_HEX_LEN:
        raise ApiError(
            status_code=422,
            error="invalid_request",
            detail=f"tx_hash 非法: {body.tx_hash!r}",
            code="bad_tx_hash",
        )
    store: CoreStore = request.app.state.store
    inserted = store.anchor_record(body.agent_id, body.digest, tx)
    return AnchorResultResponse(
        agent_id=body.agent_id, digest=body.digest, tx_hash=tx, duplicate=not inserted
    )
