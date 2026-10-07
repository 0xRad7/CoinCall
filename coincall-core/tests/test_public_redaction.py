"""公开面脱敏：catalog 与 GET /manifests/{id} 不含 endpoint.url；internal 通道全量。"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import VALID_MANIFEST

pytestmark = pytest.mark.unit


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(duckdb_path=str(tmp_path / "core.duckdb")))) as c:
        r = c.post("/manifests", json=VALID_MANIFEST)
        assert r.status_code in (200, 201)
        yield c


def test_manifest_get_redacts_url(client):
    m = client.get("/manifests/svc_translate_v1").json()["manifest"]
    assert m["endpoint"].get("url") is None
    assert m["endpoint"]["type"] == "http_json"


def test_catalog_redacts_url(client):
    body = client.get("/catalog").text
    assert "up.example" not in body and '"url": "http' not in body


def test_internal_manifest_returns_full_url(client):
    m = client.get("/internal/manifests/svc_translate_v1").json()["manifest"]
    assert m["endpoint"]["url"]


def test_internal_manifest_404(client):
    assert client.get("/internal/manifests/svc_nope").status_code == 404
