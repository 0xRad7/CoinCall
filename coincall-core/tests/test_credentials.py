"""上游凭证（endpoint credentials）：公开 manifest 与凭证分离——值加密存储、
公开面只出名字、internal resolve 供网关取用（01 §endpoint-credentials）。"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import VALID_MANIFEST


@pytest.fixture
def seeded(tmp_path):
    settings = Settings(duckdb_path=str(tmp_path / "core.duckdb"))
    with TestClient(create_app(settings)) as client:
        r = client.post("/manifests", json=VALID_MANIFEST)
        assert r.status_code in (200, 201), r.text
        yield client


@pytest.mark.unit
def test_put_roundtrip_and_masked_get(seeded):
    put = seeded.put(
        "/services/svc_translate_v1/credentials", json={"headers": {"X-API-KEY": "sk-secret-123"}}
    )
    assert put.status_code in (200, 201), put.text
    got = seeded.get("/services/svc_translate_v1/credentials").json()
    assert got["header_names"] == ["X-API-KEY"]
    assert "sk-secret-123" not in seeded.get("/services/svc_translate_v1/credentials").text


@pytest.mark.unit
def test_internal_resolve_returns_values(seeded):
    seeded.put(
        "/services/svc_translate_v1/credentials", json={"headers": {"X-API-KEY": "sk-secret-123"}}
    )
    r = seeded.get("/internal/services/svc_translate_v1/credentials")
    assert r.status_code == 200
    assert r.json()["headers"]["X-API-KEY"] == "sk-secret-123"


@pytest.mark.unit
def test_unknown_service_404(seeded):
    assert seeded.put("/services/svc_nope/credentials", json={"headers": {}}).status_code == 404


@pytest.mark.unit
def test_at_rest_encrypted(seeded, tmp_path):
    seeded.put(
        "/services/svc_translate_v1/credentials",
        json={"headers": {"X-API-KEY": "sk-plaintext-xyz"}},
    )
    raw = (tmp_path / "core.duckdb").read_bytes()
    assert b"sk-plaintext-xyz" not in raw


@pytest.mark.unit
def test_delete_then_resolve_missing(seeded):
    seeded.put("/services/svc_translate_v1/credentials", json={"headers": {"X-API-KEY": "v"}})
    assert seeded.delete("/services/svc_translate_v1/credentials").status_code in (200, 204)
    assert seeded.get("/internal/services/svc_translate_v1/credentials").json()["headers"] == {}


@pytest.mark.unit
def test_catalog_and_manifest_never_leak(seeded):
    seeded.put(
        "/services/svc_translate_v1/credentials", json={"headers": {"X-API-KEY": "sk-leak-check"}}
    )
    for path in ("/catalog", "/manifests/svc_translate_v1"):
        assert "sk-leak-check" not in seeded.get(path).text
