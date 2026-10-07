"""Teams（10 §0.6，2026-10-07 发起人拍板）：把 认领/身份/AgentID 包装成团队产品形态。

层级：钱包(1) → Teams(N) → 服务(N)。Team ≈ provider 记录换皮（agent_id 退化为内部字段）：
- `GET /providers/mine?wallet=` —— 连接钱包反查我的团队（问题①：不让用户手输 Agent ID）；
- `POST /teams/prepare` —— 创建团队第一步：平台代发铸新身份，回 agent_id（绑定+登记由
  前端用既有 8010 setAgentWallet + POST /providers 完成，一次钱包签名呈现在 UI 上即
  「确认创建团队」）；
- `GET /teams/{agent_id}` —— Team 主页聚合：团队信息 + 服务列表 + 收入/履约/反馈汇总
  （收入=该团队服务全部收款钱包的 Charged 聚合；链上按钱包记账，跨团队共享钱包属边缘
  场景，响应中透出钱包集以便核对）。
"""

import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ApiError
from app.storage.db import CoreStore

router = APIRouter(tags=["teams"])

REGISTER_POLL_INTERVAL_S = 1.0
REGISTER_POLL_TIMEOUT_S = 30.0


class TeamPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=128)
    origin: str | None = Field(
        default=None,
        description="控制台访问源（http/https）；构造内网可达的链上 agentURI，缺省回退占位域",
    )


def _store(request: Request) -> CoreStore:
    return request.app.state.store


@router.get("/providers/mine")
def my_teams(wallet: str, request: Request) -> dict[str, Any]:
    """连接钱包 → 我的团队列表（claim_wallet 反查；含各团队服务数）。"""
    rows = _store(request).list_providers()
    mine = [r for r in rows if str(r.get("claim_wallet") or "").lower() == wallet.lower()]
    services = _store(request).list_services()
    for t in mine:
        t["service_count"] = sum(
            1
            for s in services
            if (s["manifest"].get("provider") or {}).get("agent_id") == t["agent_id"]
        )
    return {"wallet": wallet, "teams": mine}


@router.post("/teams/prepare", status_code=201)
def team_prepare(body: TeamPrepareRequest, request: Request) -> dict[str, Any]:
    """创建团队①：平台代发铸造新 ERC-8004 身份（登记名=团队名作 agentURI 附注）。

    返回 agent_id 后，前端走既有链路完成：钱包签 AgentWalletSet → 8010 绑定 →
    POST /providers {agent_id, display_name, claim_wallet}。gas 由出资账户承担。
    """
    http: httpx.Client = request.app.state.mint_http
    base = request.app.state.app_settings.bot_chain_api_base_url.rstrip("/")
    origin = (body.origin or "").rstrip("/")
    if origin and not origin.startswith(("http://", "https://")):
        raise ApiError(
            status_code=422,
            error="invalid_request",
            detail="origin 必须 http/https",
            code="invalid_origin",
        )
    agent_uri = f"{origin}/#/provider" if origin else "https://coincall.local/#/provider"
    try:
        resp = http.post(
            f"{base}/api/v1/agent-identity/register",
            json={
                "agent_uri": agent_uri,
                "dry_run": False,
            },
        )
        resp.raise_for_status()
        tx_hash = str(resp.json().get("tx_hash") or "")
        if not tx_hash:
            raise ValueError("register 未返回 tx_hash")
    except (httpx.HTTPError, ValueError) as exc:
        raise ApiError(
            status_code=502,
            error="identity_mint_failed",
            detail=f"团队身份铸造失败: {exc}",
            code="identity_mint_failed",
        ) from exc

    deadline = time.monotonic() + REGISTER_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        r = http.get(f"{base}/api/v1/agent-identity/register-result/{tx_hash}")
        r.raise_for_status()
        data = r.json()
        if data.get("found") and data.get("agent_ids"):
            agent_id = int(data["agent_ids"][0])
            return {
                "agent_id": agent_id,
                "tx_hash": tx_hash,
                "bind_required": True,
                "next": "钱包签名 AgentWalletSet(newWallet=连接地址) → 绑定 → POST /providers",
            }
        time.sleep(REGISTER_POLL_INTERVAL_S)
    raise ApiError(
        status_code=504,
        error="identity_mint_timeout",
        detail=f"铸造交易 {tx_hash} 未在 {REGISTER_POLL_TIMEOUT_S:.0f}s 内上链",
        code="identity_mint_timeout",
    )


@router.get("/teams/{agent_id}")
def team_profile(agent_id: int, request: Request) -> dict[str, Any]:
    """Team 主页聚合：团队 + 服务列表 + 收入/履约/反馈汇总（双源降级不 500）。"""
    team = _store(request).get_provider(agent_id)
    if team is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"team 不存在: {agent_id}",
            code="team_not_found",
        )
    services = [
        s
        for s in _store(request).list_services()
        if (s["manifest"].get("provider") or {}).get("agent_id") == agent_id
    ]
    wallets = sorted(
        {
            str((s["manifest"].get("provider") or {}).get("wallet", "")).lower()
            for s in services
            if (s["manifest"].get("provider") or {}).get("wallet")
        }
    )

    # 收入： Charged 按钱包记账（团队=其服务收款钱包集合）
    revenue_rows = _store(request).charged_by_provider()
    revenue_raw = sum(
        int(r["revenue_raw"]) for r in revenue_rows if r["provider"].lower() in wallets
    )
    revenue_count = sum(
        int(r["charged_count"]) for r in revenue_rows if r["provider"].lower() in wallets
    )

    degraded: list[str] = []
    fulfillment: dict[str, Any] = {"services": []}
    try:
        stats: Any = request.app.state.gateway_stats
        view = stats.stats_view()
        by_id = {s["service_id"]: s for s in view.get("services", [])}
        fulfillment = {
            "services": [
                {
                    k: by_id[s["service_id"]].get(k)
                    for k in (
                        "service_id",
                        "calls_success",
                        "calls_aborted",
                        "p50_ms",
                        "p95_ms",
                        "distinct_payers",
                        "last_activity_at",
                    )
                }
                for s in services
                if s["service_id"] in by_id
            ]
        }
    except Exception:
        degraded.append("gateway_stats_failed")

    feedback: dict[str, Any] = {"services": []}
    for s in services:
        rows = _store(request).list_feedback(s["service_id"])
        if rows:
            feedback["services"].append(
                {
                    "service_id": s["service_id"],
                    "count": len(rows),
                    "avg": round(sum(int(r["rating"]) for r in rows) / len(rows), 2),
                }
            )

    return {
        "team": team,
        "services": services,
        "revenue": {
            "total_raw": revenue_raw,
            "charged_count": revenue_count,
            "wallets": wallets,
            "proof": "/leaderboard/providers/{wallet}/proof",
        },
        "fulfillment": fulfillment,
        "feedback": feedback,
        "degraded": degraded,
    }
