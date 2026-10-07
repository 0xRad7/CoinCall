"""keeper 可观测端点：GET /internal/keeper/status（演示第三幕大屏直读）
+ GET /internal/keeper/reconcile（审计 F-01 缓解：Charged ↔ settle_queue 对账）。

keeper_enabled 时 status 另含 anchor 段（决策摘要锚定任务快照，10 §1/§2）。
reconcile 不自动周期跑：按需调用/监控脚本拉取（保持 keeper 结算循环零改动），
最近一次摘要挂 status.last_reconcile。
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.modules.keeper import Keeper, SettleChainError

router = APIRouter(prefix="/internal/keeper", tags=["keeper"])

#: 对账窗口上限（小时）= 30 天，与 stats 路由 window_hours 上限同量级口径
RECONCILE_MAX_WINDOW_HOURS = 720.0


@router.get("/status")
def keeper_status(request: Request) -> dict[str, Any]:
    keeper: Keeper = request.app.state.keeper
    snapshot = keeper.status_snapshot()
    anchor = getattr(request.app.state, "anchor", None)
    if anchor is not None:
        snapshot["anchor"] = anchor.status_snapshot()
    return snapshot


@router.get("/reconcile")
async def keeper_reconcile(
    request: Request,
    window_hours: float = Query(default=24.0, gt=0.0, le=RECONCILE_MAX_WINDOW_HOURS),
    from_block: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """审计 F-01 缓解（payvault-security-audit §2 运营前置 1）：

    PayVault Charged 链上事件与 settle_queue 近窗行按 (provider, from, value, nonce)
    逐笔比对（部署冒烟 charged_matches_queue 同口径）；不平即响应 ok=false + 服务端
    WARNING 逐笔告警。纯只读：不改队列状态、不发交易。
    """
    keeper: Keeper = request.app.state.keeper
    try:
        return await keeper.reconcile(window_hours=window_hours, from_block=from_block)
    except SettleChainError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
