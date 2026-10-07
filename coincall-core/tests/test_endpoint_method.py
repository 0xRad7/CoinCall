"""endpoint.method（GET/POST）与 GET 标量约束 + probe 端点。"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import VALID_MANIFEST

pytestmark = pytest.mark.unit


@pytest.fixture
def client(tmp_path):
    settings = Settings(duckdb_path=str(tmp_path / "core.duckdb"))
    with TestClient(create_app(settings)) as c:
        yield c


def _manifest(**endpoint_over):
    m = dict(VALID_MANIFEST)  # type: ignore[arg-type]
    m["service_id"] = "svc_m"
    m["endpoint"] = {"type": "http_json", "url": "https://up.example/api", "timeout_ms": 5000}
    m["endpoint"].update(endpoint_over)
    return m


def test_method_defaults_post_and_get_accepted(client):
    assert client.post("/manifests", json=_manifest()).status_code in (200, 201)
    assert client.post("/manifests", json=_manifest(method="GET")).status_code in (200, 201)


def test_method_invalid_rejected(client):
    r = client.post("/manifests", json=_manifest(method="DELETE"))
    assert r.status_code == 422
    assert "method" in r.text


def test_get_nested_object_input_rejected(client):
    m = _manifest(method="GET")
    m["input_schema"] = {
        "type": "object",
        "properties": {"q": {"type": "string"}, "filter": {"type": "object"}},
    }
    r = client.post("/manifests", json=m)
    assert r.status_code == 422
    assert "GET" in r.text and "嵌套" in r.text


def test_get_array_of_scalars_ok(client):
    m = _manifest(method="GET")
    m["input_schema"] = {
        "type": "object",
        "properties": {"tag": {"type": "array", "items": {"type": "string"}}},
    }
    assert client.post("/manifests", json=m).status_code in (200, 201)


def test_probe_roundtrip(tmp_path):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"result": "ok", "n": 3})

    app_settings = Settings(duckdb_path=str(tmp_path / "core.duckdb"))
    app = create_app(app_settings, probe_http=httpx.Client(transport=httpx.MockTransport(handler)))
    with TestClient(app) as c:
        r = c.post(
            "/services/probe",
            json={
                "url": "https://up.example/api",
                "method": "GET",
                "query": {"q": "hi"},
                "headers": {"X-API-KEY": "sk-probe"},
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status_code"] == 200 and body["body"]["n"] == 3
    assert captured["method"] == "GET" and "q=hi" in captured["url"]
    sent = {k.lower(): v for k, v in captured["headers"].items()}
    assert sent.get("x-api-key") == "sk-probe"


def test_probe_schemes_guardrail(tmp_path):
    with TestClient(create_app(Settings(duckdb_path=str(tmp_path / "core.duckdb")))) as c:
        r1 = c.post("/services/probe", json={"url": "ftp://x", "method": "GET"})
        r2 = c.post("/services/probe", json={"url": "http://192.168.0.1/x", "method": "GET"})
        assert r1.status_code == 422 and r2.status_code in (403, 422)
