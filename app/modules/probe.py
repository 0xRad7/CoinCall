"""服务探测端点（发布前的 schema 自动识别支撑，2026-10-06 增补）。

浏览器直连三方接口会被 CORS 拦截，探测必须由服务端代发。护栏（诚实边界，
hackathon 本机规模）：仅 http/https；拒绝 obvious 私网/回环字面量主机；
超时硬顶 20s；内联认证头仅本次请求使用、不落盘。
"""

import ipaddress
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

router = APIRouter(tags=["probe"])

PROBE_TIMEOUT_S = 20.0
_BODY_PREVIEW = 4096


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(description="上游完整 URL（http/https）")
    method: str = Field(default="POST", description="GET | POST")
    query: dict[str, str] = Field(default_factory=dict, description="GET 时的示例 query 参数")
    body: dict | list | None = Field(default=None, description="POST 时的示例 JSON 请求体")
    headers: dict[str, str] = Field(default_factory=dict, description="探测用认证头（不保存）")

    @field_validator("method")
    @classmethod
    def _method(cls, v: str) -> str:
        if v not in ("GET", "POST"):
            raise ValueError("method 仅支持 GET|POST")
        return v


def _guard(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="probe_url_scheme_invalid")
    host = parsed.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise HTTPException(status_code=403, detail="probe_host_forbidden")
    except ValueError:
        lowered = host.lower()
        if lowered in ("localhost",) or lowered.endswith(".local"):
            raise HTTPException(status_code=403, detail="probe_host_forbidden") from None


@router.post("/services/probe")
def probe(body: ProbeRequest, request: Request) -> dict:
    """代发一次上游请求，回状态/耗时/响应体（供前端推断 output_schema）。"""
    _guard(body.url)
    client: httpx.Client = getattr(request.app.state, "probe_http", None) or _client()
    started = time.monotonic()
    try:
        if body.method == "GET":
            resp = client.get(
                body.url,
                params=body.query,
                headers=body.headers,
                timeout=PROBE_TIMEOUT_S,
                follow_redirects=False,
            )
        else:
            resp = client.post(
                body.url,
                json=body.body or {},
                headers=body.headers,
                timeout=PROBE_TIMEOUT_S,
                follow_redirects=False,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"probe_failed: {exc}") from exc
    elapsed_ms = int((time.monotonic() - started) * 1000)
    try:
        resp_body: dict | list | str = resp.json()
    except ValueError:
        resp_body = resp.text[:_BODY_PREVIEW]
    return {
        "status_code": resp.status_code,
        "content_type": resp.headers.get("content-type", ""),
        "elapsed_ms": elapsed_ms,
        "body": resp_body,
    }


def _client() -> httpx.Client:
    return httpx.Client(trust_env=False)
