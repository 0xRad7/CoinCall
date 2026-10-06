"""步骤⑥：ProviderAdapter Protocol——转发层（02 时序⑥）。

V1 内置两种：
- InternalEchoProvider：internal:// 端点回显固定响应（测试/演示兜底，09 P1-4）；
- HttpJsonProvider：httpx POST manifest.endpoint.url（真实外联属 W4 接线）。
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.modules.manifest_client import ManifestInfo


@dataclass
class ProviderResult:
    status_code: int
    body: Any


class ProviderError(Exception):
    """Provider 非 2xx / 超时（02 时序⑥：aborted，零扣款，502）。"""


class ProviderAdapter(Protocol):
    name: str

    async def forward(
        self,
        manifest: ManifestInfo,
        body: Any,
        upstream_headers: Mapping[str, str] | None = None,
    ) -> ProviderResult: ...


class InternalEchoProvider:
    """internal 型端点：网关本地执行，回显固定响应（demo 兜底，避免网络单点）。"""

    name = "internal-echo"

    async def forward(
        self,
        manifest: ManifestInfo,
        body: Any,
        upstream_headers: Mapping[str, str] | None = None,
    ) -> ProviderResult:
        del upstream_headers  # internal 端点无上游凭证概念
        return ProviderResult(
            status_code=200,
            body={
                "service_id": manifest.service_id,
                "provider_agent_id": manifest.manifest.provider.agent_id,
                "echo": body,
            },
        )


def _query_params(body: Any) -> dict[str, Any]:
    """GET：消费者 JSON 参数 → query。标量直传、数组同 key 重复、嵌套对象拒绝。"""
    if not isinstance(body, dict):
        raise ProviderError("GET 服务的请求体必须是 JSON 对象")
    params: dict[str, Any] = {}
    for k, v in body.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            params[str(k)] = "" if v is None else v
        elif isinstance(v, list) and all(isinstance(i, (str, int, float, bool)) for i in v):
            params[str(k)] = v
        else:
            raise ProviderError(f"GET 参数 {k} 含嵌套对象/非标量数组，不支持（改用 POST）")
    return params


PROVIDER_2XX_BASE = 2  # 2xx 判定基数


class HttpJsonProvider:
    """http_json 型端点：POST JSON → JSON，超时按 manifest（硬顶 60s 在 core 校验）。"""

    name = "http-json"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def forward(
        self,
        manifest: ManifestInfo,
        body: Any,
        upstream_headers: Mapping[str, str] | None = None,
    ) -> ProviderResult:
        if not manifest.manifest.endpoint.url:
            raise ProviderError("manifest.endpoint.url 缺失")
        timeout_s = manifest.manifest.endpoint.timeout_ms / 1000
        started = time.monotonic()
        try:
            if manifest.manifest.endpoint.method == "GET":
                resp = await self.http.get(
                    manifest.manifest.endpoint.url,
                    params=_query_params(body),
                    timeout=timeout_s,
                    headers=dict(upstream_headers) if upstream_headers else None,
                )
            else:
                resp = await self.http.post(
                    manifest.manifest.endpoint.url,
                    json=body,
                    timeout=timeout_s,
                    headers=dict(upstream_headers) if upstream_headers else None,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider 超时/网络错误: {exc}") from exc
        if resp.status_code // 100 != PROVIDER_2XX_BASE:
            raise ProviderError(f"provider 返回 {resp.status_code}")
        del started  # 延迟统计在路由层统一做
        try:
            return ProviderResult(status_code=resp.status_code, body=resp.json())
        except ValueError as exc:
            raise ProviderError("provider 响应非 JSON") from exc
