"""M10 水龙头：status 探测（/info 透传）+ claim（Turnstile token 可选，否则手动指引）。

探测结论（results/d3_probes/SUMMARY.md）：claim 强制 Cloudflare Turnstile，
服务端无法全自动领水；claim 支持用户从浏览器取 token 后代发。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from httpx import Client, HTTPError
from pydantic import BaseModel, Field

from app.core.deps import ChainDep
from app.core.errors import ChainError

router = APIRouter(prefix="/faucet", tags=["M10 faucet"])

HTTP_OK = 200


def _faucet_client(request: Request) -> Client:
    return request.app.state.faucet


FaucetClientDep = Annotated[Client, Depends(_faucet_client)]


class FaucetAsset(BaseModel):
    token_symbol: str
    token_type: str
    claim_amount: str
    cooldown_hours: float
    enabled: bool
    token_address: str | None = None


class FaucetStatus(BaseModel):
    available: bool
    automatable: bool
    chain_name: str | None = None
    assets: list[FaucetAsset] = Field(default_factory=list)
    manual_url: str
    note: str


class ClaimRequest(BaseModel):
    address: str
    asset: str = "BOT"
    turnstile_token: str | None = Field(
        default=None,
        description="浏览器完成 Turnstile 后取得的 token；缺省不代发，返回手动指引",
    )
    dry_run: bool = True


class ClaimResult(BaseModel):
    claimed: bool
    code: int | None = None
    message: str
    manual_url: str
    hint: str = ""


@router.get("/status", response_model=FaucetStatus)
def faucet_status(chain: ChainDep, client: FaucetClientDep) -> FaucetStatus:
    """探测：faucet API 是否可用 + 资产/限额/冷却（/info 免验证开放）。"""
    try:
        resp = client.get("/info")
        data = resp.json().get("data", {}) if resp.status_code == HTTP_OK else {}
    except HTTPError as exc:
        raise ChainError(f"faucet API 不可达: {exc}") from exc
    assets = [
        FaucetAsset(
            token_symbol=str(a.get("token_symbol", "")),
            token_type=str(a.get("token_type", "")),
            claim_amount=str(a.get("claim_amount", "")),
            cooldown_hours=float(a.get("cooldown_hours", 0)),
            enabled=bool(a.get("enabled", False)),
            token_address=a.get("token_address") or None,
        )
        for a in data.get("assets", [])
    ]
    return FaucetStatus(
        available=bool(data),
        automatable=False,  # 探测定案：Turnstile 强制，服务端不可全自动
        chain_name=data.get("chain_name"),
        assets=assets,
        manual_url=chain.faucet_url,
        note="claim 需浏览器 Turnstile 验证；本服务可携 token 代发（POST /faucet/claim）",
    )


@router.post(
    "/claim",
    response_model=ClaimResult,
    description="代领水：需浏览器取得的 turnstile_token；dry_run=true 默认只回显将提交的请求",
)
def faucet_claim(request: ClaimRequest, chain: ChainDep, client: FaucetClientDep) -> ClaimResult:
    manual = chain.faucet_url
    manual_hint = (
        f"请到 {manual} 页面完成人机验证后领取（BOT 与 tUSDT 各一份）；"
        "或从浏览器开发者工具复制 turnstileToken 传入本接口代发"
    )
    if request.dry_run or not request.turnstile_token:
        return ClaimResult(
            claimed=False,
            message=(
                "未代发：dry_run 或缺少 turnstile_token（Cloudflare Turnstile 强制，无法全自动）"
            ),
            manual_url=manual,
            hint=manual_hint,
        )
    try:
        resp = client.post(
            "/claim",
            json={
                "address": request.address,
                "turnstileToken": request.turnstile_token,
                "asset": request.asset,
            },
        )
        body: dict[str, Any] = resp.json()
    except HTTPError as exc:
        raise ChainError(f"faucet claim 请求失败: {exc}") from exc
    code = body.get("code")
    if resp.status_code == HTTP_OK and code == 0:
        return ClaimResult(claimed=True, code=0, message="领水成功", manual_url=manual)
    return ClaimResult(
        claimed=False,
        code=code,
        message=str(body.get("message", "领水失败")),
        manual_url=manual,
        hint=manual_hint,
    )
