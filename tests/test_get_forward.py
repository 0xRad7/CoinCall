"""http_json GET 转发：JSON 参数→query 映射（标量/数组重复/嵌套拒绝）。"""

import asyncio

import httpx
import pytest

from app.modules.providers import HttpJsonProvider, ProviderError
from tests.conftest import make_manifest

pytestmark = pytest.mark.unit


def _capture(captured: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        return httpx.Response(200, json={"ok": 1})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)


def _get_manifest():
    return make_manifest(
        endpoint={
            "type": "http_json",
            "url": "https://up.example/api",
            "method": "GET",
            "timeout_ms": 5000,
        }
    )


def test_get_maps_scalars_to_query():
    captured: dict = {}
    p = HttpJsonProvider(http=_capture(captured))
    asyncio.run(p.forward(_get_manifest(), {"q": "hi", "n": 3, "flag": True}))
    assert captured["method"] == "GET"
    assert "q=hi" in captured["url"] and "n=3" in captured["url"]


def test_get_array_repeats_key():
    captured: dict = {}
    p = HttpJsonProvider(http=_capture(captured))
    asyncio.run(p.forward(_get_manifest(), {"tag": ["a", "b"]}))
    assert "tag=a" in captured["url"] and "tag=b" in captured["url"]


def test_get_nested_rejected():
    captured: dict = {}
    p = HttpJsonProvider(http=_capture(captured))
    with pytest.raises(ProviderError, match="嵌套"):
        asyncio.run(p.forward(_get_manifest(), {"filter": {"x": 1}}))


def test_get_injects_credentials_too():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"ok": 1})

    p = HttpJsonProvider(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    )
    asyncio.run(p.forward(_get_manifest(), {"q": "x"}, upstream_headers={"X-API-KEY": "sk-1"}))
    sent = {k.lower(): v for k, v in captured["headers"].items()}
    assert sent.get("x-api-key") == "sk-1"
