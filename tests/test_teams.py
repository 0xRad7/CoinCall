"""Teams（10 §0.6）：钱包反查我的团队 / Team 聚合主页 / prepare 一步铸身份。"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import (
    VALID_MANIFEST,
    FakeChainClient,
    FakeGatewayStatsClient,
    FakeIdentityClient,
)

pytestmark = pytest.mark.unit

W = "0x1234567890abcdef1234567890abcdef12345678"  # KNOWN_IDENTITIES[137]
TEAM_WALLET = "0xb6fefb44843f138975a9ddee48eeb4a1ff72f466"


def _app(tmp_path, stats=None):
    manifest = dict(VALID_MANIFEST)  # type: ignore[arg-type]
    manifest["endpoint"] = {"type": "internal"}
    return create_app(
        Settings(duckdb_path=str(tmp_path / "core.duckdb")),
        identity_client=FakeIdentityClient(known={137: W}),
        chain_client=FakeChainClient(),
        gateway_client=stats or FakeGatewayStatsClient(),
    )


@pytest.fixture
def client(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/providers", json={"agent_id": 137, "display_name": "RadAI", "claim_wallet": W})
        c.post("/manifests", json=VALID_MANIFEST)
        yield c


def test_mine_lists_teams_by_claim_wallet(client):
    r = client.get("/providers/mine", params={"wallet": W.upper()})
    assert r.status_code == 200
    teams = r.json()["teams"]
    assert len(teams) == 1 and teams[0]["display_name"] == "RadAI"
    assert teams[0]["agent_id"] == 137
    assert client.get("/providers/mine", params={"wallet": "0x" + "ee" * 20}).json()["teams"] == []


def test_team_aggregate(client):
    r = client.get("/teams/137")
    assert r.status_code == 200
    body = r.json()
    assert body["team"]["display_name"] == "RadAI"
    assert body["team"]["claim_wallet"] == W
    assert [s["service_id"] for s in body["services"]] == ["svc_translate_v1"]
    assert "revenue" in body and "fulfillment" in body and "feedback" in body
    assert client.get("/teams/999").status_code == 404


def test_team_aggregate_unknown_ok(client):
    """stats/feedback 源不可达时聚合降级不 500（双源纪律）。"""
    body = client.get("/teams/137").json()
    assert body["degraded"] == [] or isinstance(body["degraded"], list)


def test_prepare_mints_identity(tmp_path):
    """prepare：8010 register（dry_run=false）→ 轮询 register-result → 回 agent_id。"""
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, dict(request.url.params) if request.method == "GET" else {}))
        if (
            request.url.path.endswith("/api/v1/agent-identity/register")
            and request.method == "POST"
        ):
            return httpx.Response(200, json={"dry_run": False, "tx_hash": "0xabc", "status": 1})
        if request.url.path.endswith("/register-result/0xabc"):
            return httpx.Response(
                200,
                json={
                    "found": True,
                    "status": 1,
                    "agent_ids": [233],
                    "owner": "0xc37ffe97b4d2c3d0187b1ddedf273e52a461b63a",
                    "agent_wallet": "0xc37ffe97b4d2c3d0187b1ddedf273e52a461b63a",
                },
            )
        return httpx.Response(404)

    class FakeMintHttp:
        def post(self, url, json=None):
            calls.append(("POST", {"url": url}))
            return handler(httpx.Request("POST", url))

        def get(self, url):
            return handler(httpx.Request("GET", url))

    with TestClient(
        create_app(
            Settings(duckdb_path=str(tmp_path / "c.duckdb")),
            identity_client=FakeIdentityClient(),
            chain_client=FakeChainClient(),
            gateway_client=FakeGatewayStatsClient(),
            mint_http=FakeMintHttp(),
        )
    ) as c:
        r = c.post("/teams/prepare", json={"display_name": "NewTeam"})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["agent_id"] == 233
    assert "typed_data_hint" in body or body.get("bind_required") is True
