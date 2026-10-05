"""keeper 可观测端点：GET /internal/keeper/status（演示第三幕大屏直读）。"""

from typing import Any

from fastapi import APIRouter, Request

from app.modules.keeper import Keeper

router = APIRouter(prefix="/internal/keeper", tags=["keeper"])


@router.get("/status")
def keeper_status(request: Request) -> dict[str, Any]:
    keeper: Keeper = request.app.state.keeper
    return keeper.status_snapshot()
