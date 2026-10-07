"""上游凭证注入（01 §endpoint-credentials）：转发时叠加 core 的凭证头；
消费者入站头（X-Api-Key/X-PAYMENT 等）一律不上透传。"""

import asyncio

import httpx
import pytest

from app.modules.providers import HttpJsonProvider
from tests.conftest import make_manifest

pytestmark = pytest.mark.unit


def _capture_client(captured: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"ok": 1})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)


@pytest.mark.unit
def test_forward_injects_credentials_and_strips_consumer_headers() -> None:
    captured: dict = {}
    provider = HttpJsonProvider(http=_capture_client(captured))
    manifest = make_manifest(
        endpoint={"type": "http_json", "url": "https://upstream.example/api", "timeout_ms": 30000}
    )
    asyncio.run(
        provider.forward(manifest, {"q": "hi"}, upstream_headers={"X-API-KEY": "sk-upstream-1"})
    )
    sent = {k.lower(): v for k, v in captured["headers"].items()}
    assert sent.get("x-api-key") == "sk-upstream-1"
    # 消费者侧头不透传（当前实现本就不转发入站头——本断言锁死该语义不被回归）
    assert "x-payment" not in sent
    assert "x-idempotency-key" not in sent


@pytest.mark.unit
def test_forward_without_credentials_sends_no_extra_headers() -> None:
    captured: dict = {}
    provider = HttpJsonProvider(http=_capture_client(captured))
    manifest = make_manifest(
        endpoint={"type": "http_json", "url": "https://upstream.example/api", "timeout_ms": 30000}
    )
    asyncio.run(provider.forward(manifest, {"q": "hi"}))
    sent = {k.lower() for k in captured["headers"]}
    assert "x-api-key" not in sent
