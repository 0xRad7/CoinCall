"""团队级上游认证头：同表异键 team:{agent_id}，服务级优先、团队级回退（网关解析）。"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import VALID_MANIFEST, FakeIdentityClient

pytestmark = pytest.mark.unit

W = "0x1234567890abcdef1234567890abcdef12345678"


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(
            Settings(duckdb_path=str(tmp_path / "c.duckdb")),
            identity_client=FakeIdentityClient(known={137: W}),
        )
    ) as c:
        c.post("/providers", json={"agent_id": 137, "display_name": "T", "claim_wallet": W})
        yield c


def test_team_credentials_roundtrip_masked(client):
    r = client.put("/teams/137/credentials", json={"headers": {"X-API-KEY": "sk-team"}})
    assert r.status_code == 200, r.text
    got = client.get("/teams/137/credentials").json()
    assert got["header_names"] == ["X-API-KEY"]
    assert "sk-team" not in client.get("/teams/137/credentials").text
    # internal resolve 出明文
    assert client.get("/internal/teams/137/credentials").json()["headers"]["X-API-KEY"] == "sk-team"


def test_team_credentials_404_unknown_team(client):
    assert client.put("/teams/999/credentials", json={"headers": {}}).status_code == 404


def test_team_credentials_isolated_from_services(client):
    client.post("/manifests", json=VALID_MANIFEST)
    client.put("/services/svc_translate_v1/credentials", json={"headers": {"A": "1"}})
    client.put("/teams/137/credentials", json={"headers": {"B": "2"}})
    assert client.get("/internal/teams/137/credentials").json()["headers"] == {"B": "2"}
    assert client.get("/internal/services/svc_translate_v1/credentials").json()["headers"] == {
        "A": "1"
    }
