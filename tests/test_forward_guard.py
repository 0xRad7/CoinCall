"""P0 堵洞：转发路径 SSRF 护栏 + https 强制（api-security-probe.md L0-1/L0-2）。"""

import asyncio

import httpx
import pytest

from app.modules.providers import HttpJsonProvider, ProviderError
from tests.conftest import make_manifest

pytestmark = pytest.mark.unit


def _manifest(url: str):
    return make_manifest(endpoint={"type": "http_json", "url": url, "timeout_ms": 5000})


def _capture(captured: dict, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(status, json={"ok": 1})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8010/api/v1/chains",
        "http://localhost:8020/apikeys",
        "http://192.168.1.1/api",
        "http://10.0.0.1/api",
        "http://172.16.0.1/api",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]:8010/x",
    ],
)
def test_forward_blocks_private_targets(url: str) -> None:
    captured: dict = {}
    p = HttpJsonProvider(http=_capture(captured))
    with pytest.raises(ProviderError, match=r"SSRF|私网|回环"):
        asyncio.run(p.forward(_manifest(url), {"q": 1}))


def test_forward_blocks_plain_http_public() -> None:
    """L0-2：公网 http:// 明文也拒（certifi 校验只对 https 有意义）。"""
    captured: dict = {}
    p = HttpJsonProvider(http=_capture(captured))
    with pytest.raises(ProviderError, match="https"):
        asyncio.run(p.forward(_manifest("http://api.example.com/v1"), {"q": 1}))


def test_forward_allows_https_public() -> None:
    captured: dict = {}
    p = HttpJsonProvider(http=_capture(captured))
    asyncio.run(p.forward(_manifest("https://api.example.com/v1"), {"q": 1}))
    assert "api.example.com" in captured["url"]


def test_forward_loopback_exception_via_flag() -> None:
    """回环例外仅经显式 env（本机 demo http 服务），默认关。"""
    import os

    captured: dict = {}
    p = HttpJsonProvider(
        http=_capture(captured), allow_loopback=(os.environ.get("COINCALL_ALLOW_LOOPBACK") == "1")
    )
    if os.environ.get("COINCALL_ALLOW_LOOPBACK") == "1":
        asyncio.run(p.forward(_manifest("http://127.0.0.1:9999/x"), {}))
    else:
        with pytest.raises(ProviderError):
            asyncio.run(p.forward(_manifest("http://127.0.0.1:9999/x"), {}))
