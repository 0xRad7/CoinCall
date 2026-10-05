"""步骤②：查服务——manifest 取自 core（60s 内存缓存，01 §5），镜像冻结契约 #1 的 JSON 形态。

网关与 core 分仓部署，不共享 Python 包：此处模型是对
coincall-core `app/modules/manifest.py` 序列化 JSON 的**读取侧镜像**，
字段以 01 节契约为准（冻结点在 core 侧，此处只读）。
"""

import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

HTTP_OK = 200


class ManifestPricing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: str = "USDT"  # noqa: S105 —— 计价 token 符号，非凭据
    model: str = "per_call"
    amount: str
    amount_raw: str


class ManifestEndpoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str  # http_json | internal
    url: str | None = None
    timeout_ms: int = 30_000


class ManifestProvider(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_id: int
    wallet: str
    display_name: str


class ManifestView(BaseModel):
    """ServiceManifest 正文（core 序列化形态的读取镜像）。"""

    model_config = ConfigDict(extra="ignore")

    service_id: str
    name: str
    version: str
    provider: ManifestProvider
    endpoint: ManifestEndpoint
    pricing: ManifestPricing
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    status: str = "active"


class ManifestInfo(BaseModel):
    """core GET /manifests/{id} 响应。"""

    model_config = ConfigDict(extra="ignore")

    service_id: str
    manifest: ManifestView
    status: str = "active"
    manifest_hash: str = ""


class ServiceNotFoundError(Exception):
    """服务不存在或 status != active（02 时序②：均 404）。"""

    def __init__(self, service_id: str) -> None:
        super().__init__(service_id)
        self.service_id = service_id


class ManifestClient:
    """core manifest 读取 + TTL 缓存。"""

    def __init__(
        self,
        base_url: str,
        http: httpx.AsyncClient,
        cache_ttl_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, ManifestInfo]] = {}

    async def get(self, service_id: str) -> ManifestInfo:
        now = time.monotonic()
        hit = self._cache.get(service_id)
        if hit is not None and now - hit[0] < self.cache_ttl:
            info = hit[1]
        else:
            try:
                resp = await self.http.get(f"{self.base_url}/manifests/{service_id}")
            except httpx.HTTPError as exc:
                raise ServiceNotFoundError(service_id) from exc
            if resp.status_code != HTTP_OK:
                raise ServiceNotFoundError(service_id)
            info = ManifestInfo.model_validate(resp.json())
            self._cache[service_id] = (now, info)
        if info.status != "active" or info.manifest.status != "active":
            raise ServiceNotFoundError(service_id)
        return info

    def invalidate(self, service_id: str) -> None:
        self._cache.pop(service_id, None)
