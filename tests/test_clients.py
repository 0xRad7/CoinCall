"""core 客户端（auth/manifest）HTTP 行为：respx 方法级 mock（C-06）。"""

import httpx
import pytest
import respx
from app.modules.auth import AuthError, CoreAuthClient
from app.modules.manifest_client import ManifestClient, ServiceNotFoundError

from tests.conftest import API_KEY, make_manifest

pytestmark = pytest.mark.unit

CORE = "http://core.test"
VALIDATE_URL = f"{CORE}/internal/apikeys/validate"
MANIFEST_URL = f"{CORE}/manifests/"


@respx.mock
async def test_auth_client_validate_ok() -> None:
    respx.post(VALIDATE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "key_id": "key_1",
                "consumer_wallet": "0x9858EfFD232B4033E47d90003D41EC34EcaEda94",
                "quota_raw": None,
                "status": "active",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = CoreAuthClient(base_url=CORE, http=http)
        info = await client.validate(API_KEY)
    assert info.key_id == "key_1"
    assert info.status == "active"


@respx.mock
async def test_auth_client_validate_rejects_401() -> None:
    respx.post(VALIDATE_URL).mock(
        return_value=httpx.Response(401, json={"error": "unauthorized", "code": "apikey_unknown"})
    )
    async with httpx.AsyncClient() as http:
        client = CoreAuthClient(base_url=CORE, http=http)
        with pytest.raises(AuthError) as exc_info:
            await client.validate("cck_bad")
    assert exc_info.value.code == "apikey_unknown"


@respx.mock
async def test_auth_client_core_down_fails_closed() -> None:
    respx.post(VALIDATE_URL).mock(side_effect=httpx.ConnectError("core down"))
    async with httpx.AsyncClient() as http:
        client = CoreAuthClient(base_url=CORE, http=http)
        with pytest.raises(AuthError) as exc_info:
            await client.validate(API_KEY)
    assert exc_info.value.code == "core_unavailable"


@respx.mock
async def test_manifest_client_get_ok_and_cached() -> None:
    route = respx.get(f"{MANIFEST_URL}svc_translate_v1").mock(
        return_value=httpx.Response(200, json=make_manifest().model_dump())
    )
    async with httpx.AsyncClient() as http:
        client = ManifestClient(base_url=CORE, http=http)
        first = await client.get("svc_translate_v1")
        await client.get("svc_translate_v1")  # 60s TTL 内走缓存
    assert first.service_id == "svc_translate_v1"
    assert first.manifest.pricing.amount_raw == "10000"
    assert route.call_count == 1  # 缓存命中


@respx.mock
async def test_manifest_client_cache_ttl_zero() -> None:
    route = respx.get(f"{MANIFEST_URL}svc_translate_v1").mock(
        return_value=httpx.Response(200, json=make_manifest().model_dump())
    )
    async with httpx.AsyncClient() as http:
        client = ManifestClient(base_url=CORE, http=http, cache_ttl_seconds=0.0)
        await client.get("svc_translate_v1")
        await client.get("svc_translate_v1")
    assert route.call_count == 2


@respx.mock
async def test_manifest_client_404() -> None:
    respx.get(f"{MANIFEST_URL}svc_nope").mock(
        return_value=httpx.Response(404, json={"error": "not_found"})
    )
    async with httpx.AsyncClient() as http:
        client = ManifestClient(base_url=CORE, http=http)
        with pytest.raises(ServiceNotFoundError):
            await client.get("svc_nope")
