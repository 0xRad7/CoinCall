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


def test_get_retries_once_on_connection_drop():
    """GET 上游连接级断连（冷启动/隧道抖动）静默重试一次后成功。

    2026-10-07 线上：ngrok 上游间歇 RemoteProtocolError（2/3 失败），消费端 502 但未扣款；
    重试对消费端透明。POST 上游不重试（防上游重复执行副作用）——由语义保证，此处锚 GET 行为。
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json={"ok": 1})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    p = HttpJsonProvider(http=http)
    result = asyncio.run(p.forward(_get_manifest(), {"q": "hi"}))
    assert result.status_code == 200 and result.body == {"ok": 1}
    assert calls["n"] == 2  # 第一次断连 + 第二次成功


def test_get_both_attempts_drop_raises_provider_error():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.RemoteProtocolError("drop")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    p = HttpJsonProvider(http=http)
    with pytest.raises(ProviderError, match="provider 超时/网络错误"):
        asyncio.run(p.forward(_get_manifest(), {"q": "hi"}))
    assert calls["n"] == 2  # 重试过一次后仍失败才报 502
