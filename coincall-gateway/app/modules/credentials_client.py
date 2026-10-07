"""上游凭证客户端（01 §endpoint-credentials）：core internal resolve + 进程内 TTL 缓存。

失败语义：取不到凭证按「无凭证」转发（上游将自行 401 → provider_failed →
aborted 零扣款），只记 warning 不阻断调用路径。
"""

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class UpstreamCredentialsClient:
    """GET {core}/internal/services/{id}/credentials，60s TTL 进程内缓存。"""

    def __init__(self, base_url: str, http: httpx.AsyncClient, ttl: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.ttl = ttl
        self._cache: dict[str, tuple[float, dict[str, str]]] = {}

    async def get(self, service_id: str) -> dict[str, str] | None:
        return await self.get_for(service_id, agent_id=None)

    async def get_for(self, service_id: str, *, agent_id: int | None) -> dict[str, str] | None:
        """服务级优先；为空且服务属某团队 → 回退团队默认认证头（team:{agent_id} 同源存储）。"""
        now = time.monotonic()
        cache_key = f"{service_id}|{agent_id}"
        hit = self._cache.get(cache_key)
        if hit and now - hit[0] < self.ttl:
            return hit[1] or None
        headers = await self._fetch(service_id)
        if not headers and agent_id is not None:
            headers = await self._fetch_team(agent_id)
        self._cache[cache_key] = (now, headers)
        return headers or None

    def invalidate(self, service_id: str) -> None:
        self._cache.pop(service_id, None)

    async def _fetch_team(self, agent_id: int) -> dict[str, str]:
        try:
            resp = await self.http.get(f"{self.base_url}/internal/teams/{agent_id}/credentials")
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return {str(k): str(v) for k, v in (data.get("headers") or {}).items()}
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("团队凭证获取失败 agent=%s: %s（按无凭证转发）", agent_id, exc)
            return {}

    async def _fetch(self, service_id: str) -> dict[str, str]:
        try:
            resp = await self.http.get(
                f"{self.base_url}/internal/services/{service_id}/credentials"
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return {str(k): str(v) for k, v in (data.get("headers") or {}).items()}
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("上游凭证获取失败 service=%s: %s（按无凭证转发）", service_id, exc)
            return {}
