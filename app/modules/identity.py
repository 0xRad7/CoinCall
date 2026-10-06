"""ERC-8004 身份视图客户端（经 coincall-bot-chain-api；01 §2 步骤③的存在性校验）。"""

import time
from typing import Protocol

import httpx
from pydantic import BaseModel

from app.core.errors import ApiError

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409


class IdentityInfo(BaseModel):
    """8010 GET /api/v1/agent-identity/{id} 的裁剪视图。"""

    token_id: int
    owner: str
    agent_wallet: str
    token_uri: str = ""


class IdentityClient(Protocol):
    """身份查询协议：返回 None = 链上不存在（404）。"""

    def get(self, agent_id: int, *, force: bool = False) -> IdentityInfo | None: ...


class BotChainIdentityClient:
    """真实实现：查 8010 identity 视图；结果（含 404 负结果）按 ttl 短缓存。"""

    def __init__(self, http: httpx.Client, base_url: str, ttl: float = 60.0) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._ttl = ttl
        self._cache: dict[int, tuple[float, IdentityInfo | None]] = {}

    def get(self, agent_id: int, *, force: bool = False) -> IdentityInfo | None:
        now = time.monotonic()
        cached = self._cache.get(agent_id)
        if not force and cached is not None and now - cached[0] < self._ttl:
            return cached[1]
        try:
            resp = self._http.get(f"{self._base_url}/api/v1/agent-identity/{agent_id}")
        except httpx.HTTPError as exc:
            raise ApiError(
                status_code=502,
                error="identity_unavailable",
                detail=f"身份服务不可达（bot-chain-api）: {exc.__class__.__name__}",
                code="identity_unavailable",
            ) from exc
        if resp.status_code == HTTP_NOT_FOUND or (
            # 实测（2026-10-01）：8010 对未注册 tokenId 返回 409 tx_reverted
            # （ownerOf revert，detail 含"不存在或未注册"），并非 404——两种形态都按不存在处理
            resp.status_code == HTTP_CONFLICT and "不存在" in resp.text
        ):
            info: IdentityInfo | None = None
        elif resp.status_code == HTTP_OK:
            payload = resp.json()
            try:
                info = IdentityInfo(
                    token_id=int(payload["token_id"]),
                    owner=str(payload.get("owner", "")),
                    agent_wallet=str(payload.get("agent_wallet", "")),
                    token_uri=str(payload.get("token_uri", "") or ""),
                )
            except (KeyError, ValueError) as exc:
                raise ApiError(
                    status_code=502,
                    error="identity_unavailable",
                    detail=f"身份视图响应异常: {exc}",
                    code="identity_unavailable",
                ) from exc
        else:
            raise ApiError(
                status_code=502,
                error="identity_unavailable",
                detail=f"身份视图查询失败: HTTP {resp.status_code}",
                code="identity_unavailable",
            )
        self._cache[agent_id] = (now, info)
        return info
