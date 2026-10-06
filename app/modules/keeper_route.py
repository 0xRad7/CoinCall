"""keeper 可观测端点：GET /internal/keeper/status（演示第三幕大屏直读）。

keeper_enabled 时另含 anchor 段（决策摘要锚定任务快照，10 §1/§2）。
"""

from typing import Any

from fastapi import APIRouter, Request

from app.modules.keeper import Keeper

router = APIRouter(prefix="/internal/keeper", tags=["keeper"])


@router.get("/status")
def keeper_status(request: Request) -> dict[str, Any]:
    keeper: Keeper = request.app.state.keeper
    snapshot = keeper.status_snapshot()
    anchor = getattr(request.app.state, "anchor", None)
    if anchor is not None:
        snapshot["anchor"] = anchor.status_snapshot()
    return snapshot
