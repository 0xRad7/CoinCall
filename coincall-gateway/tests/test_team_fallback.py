"""凭证解析回退：服务级优先，空则团队级（manifest.provider.agent_id）。"""

import asyncio

import httpx
import pytest

from app.modules.credentials_client import UpstreamCredentialsClient

pytestmark = pytest.mark.unit


def _client(routes: dict[str, str]) -> UpstreamCredentialsClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for k, v in routes.items():
            if k in request.url.path:
                return httpx.Response(200, json={"headers": __import__("json").loads(v)})
        return httpx.Response(404)

    return UpstreamCredentialsClient(
        base_url="http://core",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False),
    )


def test_service_level_wins_over_team():
    c = _client(
        {
            "/internal/services/svc/": '{"X-API-KEY": "svc-key"}',
            "/internal/teams/": '{"X-API-KEY": "team-key"}',
        }
    )
    got = asyncio.run(c.get_for("svc", agent_id=170))
    assert got == {"X-API-KEY": "svc-key"}


def test_team_fallback_when_service_empty():
    c = _client(
        {
            "/internal/services/svc/": "{}",
            "/internal/teams/170": '{"X-API-KEY": "team-key"}',
        }
    )
    got = asyncio.run(c.get_for("svc", agent_id=170))
    assert got == {"X-API-KEY": "team-key"}


def test_none_when_both_empty():
    c = _client(
        {
            "/internal/services/svc/": "{}",
            "/internal/teams/170": "{}",
        }
    )
    assert asyncio.run(c.get_for("svc", agent_id=170)) is None


def test_no_agent_id_only_service():
    c = _client({"/internal/services/svc/": '{"A": "1"}'})
    assert asyncio.run(c.get_for("svc", agent_id=None)) == {"A": "1"}
