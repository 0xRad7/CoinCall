"""T21：01 §2 Provider 登记——先经 8010 校验 ERC-8004 身份存在（404→422），再落库关联。

unit：FakeIdentityClient（零网络）；live：真 8010 查真身份 agentId 162。
混合 marker 文件不设模块级 pytestmark（逐用例打标）。
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ApiError
from app.main import create_app
from app.modules.identity import BotChainIdentityClient
from tests.conftest import VALID_MANIFEST

REAL_AGENT_ID = 162  # 链上真实身份（任务书给定）
W = "0x1234567890abcdef1234567890abcdef12345678"
UNKNOWN_AGENT_ID = 999_999_999


class TestProvidersUnit:
    @pytest.mark.unit
    def test_register_validates_identity_and_stores_wallet(self, client: TestClient) -> None:
        resp = client.post(
            "/providers",
            json={
                "agent_id": 137,
                "display_name": "Team booth-demo",
                "claim_wallet": "0x1234567890abcdef1234567890abcdef12345678",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["agent_id"] == 137
        assert body["display_name"] == "Team booth-demo"
        assert body["wallet"] == client.app.state.identities.known[137].lower()
        assert client.app.state.identities.calls == [137]  # 确经 8010 身份校验

    @pytest.mark.unit
    def test_register_unknown_identity_422(self, client: TestClient) -> None:
        resp = client.post(
            "/providers",
            json={"agent_id": UNKNOWN_AGENT_ID, "display_name": "ghost", "claim_wallet": W},
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"] == "identity_not_found"
        assert body["code"] == "identity_not_found"
        assert "trace_id" in body

    @pytest.mark.unit
    def test_identity_result_cached_short_ttl(self) -> None:
        """短缓存在 BotChainIdentityClient 层（MockTransport 计数，零网络）：二查一发。"""
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "token_id": 137,
                    "owner": "0x" + "11" * 20,
                    "agent_wallet": "0x" + "22" * 20,
                    "token_uri": "",
                    "metadata": {},
                },
            )

        ident = BotChainIdentityClient(
            httpx.Client(transport=httpx.MockTransport(handler)), "http://bca.test", ttl=60.0
        )
        assert ident.get(137) is not None
        assert ident.get(137) is not None
        assert len(hits) == 1  # 第二次命中缓存

        miss_hits: list[str] = []

        def miss_handler(request: httpx.Request) -> httpx.Response:
            miss_hits.append(request.url.path)
            return httpx.Response(404, json={"detail": "not found"})

        ident404 = BotChainIdentityClient(
            httpx.Client(transport=httpx.MockTransport(miss_handler)), "http://bca.test", ttl=60.0
        )
        assert ident404.get(138) is None
        assert ident404.get(138) is None
        assert len(miss_hits) == 1  # 负结果同样短缓存

    @pytest.mark.unit
    def test_identity_409_revert_treated_as_not_found(self) -> None:
        """实测契约：8010 对未注册 tokenId 返回 409 tx_reverted（ownerOf revert）。"""

        def revert_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409, json={"error": "tx_reverted", "detail": "tokenId 138 不存在或未注册: …"}
            )

        ident = BotChainIdentityClient(
            httpx.Client(transport=httpx.MockTransport(revert_handler)), "http://bca.test"
        )
        assert ident.get(138) is None  # 409"不存在"→ identity_not_found 语义

        def other_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"error": "tx_reverted", "detail": "other revert"})

        ident_other = BotChainIdentityClient(
            httpx.Client(transport=httpx.MockTransport(other_handler)), "http://bca.test"
        )
        try:
            ident_other.get(138)
        except ApiError as exc:
            assert exc.status_code == 502  # 语义不明的 409 仍是身份服务异常
        else:
            raise AssertionError("非'不存在'语义的 409 应上抛 identity_unavailable")

    @pytest.mark.unit
    def test_register_upsert_updates_display_name(self, client: TestClient) -> None:
        client.post(
            "/providers",
            json={
                "agent_id": 137,
                "display_name": "v1",
                "claim_wallet": "0x1234567890abcdef1234567890abcdef12345678",
            },
        )
        resp = client.post(
            "/providers",
            json={
                "agent_id": 137,
                "display_name": "v2",
                "claim_wallet": "0x1234567890abcdef1234567890abcdef12345678",
            },
        )
        assert resp.status_code == 201
        listed = {p["agent_id"]: p for p in client.get("/providers").json()["providers"]}
        assert listed[137]["display_name"] == "v2"

    @pytest.mark.unit
    def test_list_providers_and_empty(self, client: TestClient) -> None:
        assert client.get("/providers").json()["providers"] == []
        client.post(
            "/providers",
            json={
                "agent_id": 137,
                "display_name": "t",
                "claim_wallet": "0x1234567890abcdef1234567890abcdef12345678",
            },
        )
        body = client.get("/providers").json()["providers"]
        assert len(body) == 1
        assert set(body[0]) == {"agent_id", "display_name", "wallet", "created_at"}

    @pytest.mark.unit
    def test_provider_services_link_to_manifests(self, client: TestClient) -> None:
        client.post(
            "/providers",
            json={
                "agent_id": 137,
                "display_name": "t",
                "claim_wallet": "0x1234567890abcdef1234567890abcdef12345678",
            },
        )
        resp = client.post("/manifests", json=VALID_MANIFEST)
        assert resp.status_code == 201, resp.text
        listed = client.get("/providers/137/services")
        assert listed.status_code == 200
        services = listed.json()["services"]
        assert [s["service_id"] for s in services] == [VALID_MANIFEST["service_id"]]
        # 其他 agent 的 manifest 不串
        other = dict(VALID_MANIFEST, service_id="svc_other_v1")
        other["provider"] = dict(other["provider"], agent_id=138)
        assert client.post("/manifests", json=other).status_code == 201
        assert len(client.get("/providers/137/services").json()["services"]) == 1

    @pytest.mark.unit
    def test_provider_services_unknown_provider_404(self, client: TestClient) -> None:
        resp = client.get("/providers/137/services")
        assert resp.status_code == 404
        assert resp.json()["code"] == "provider_not_found"

    @pytest.mark.unit
    def test_register_rejects_bad_body(self, client: TestClient) -> None:
        assert (
            client.post("/providers", json={"agent_id": 0, "display_name": "x"}).status_code == 422
        )
        assert (
            client.post(
                "/providers",
                json={
                    "agent_id": 137,
                    "display_name": "",
                    "claim_wallet": "0x1234567890abcdef1234567890abcdef12345678",
                },
            ).status_code
            == 422
        )


class TestProvidersLive:
    @pytest.mark.live
    def test_register_real_identity_162(self, tmp_path) -> None:
        """真 8010 + 真链：ERC-8004 tokenId 162 存在，登记成功且回读链上 agent_wallet。"""
        settings = Settings(duckdb_path=str(tmp_path / "live.duckdb"))
        with TestClient(create_app(settings)) as live_client:
            resp = live_client.post(
                "/providers", json={"agent_id": REAL_AGENT_ID, "display_name": "live-162"}
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["wallet"].startswith("0x") and len(body["wallet"]) == 42
            listed = live_client.get("/providers").json()["providers"]
            assert any(p["agent_id"] == REAL_AGENT_ID for p in listed)

    @pytest.mark.live
    def test_register_unknown_identity_live_422(self, tmp_path) -> None:
        settings = Settings(duckdb_path=str(tmp_path / "live2.duckdb"))
        with TestClient(create_app(settings)) as live_client:
            resp = live_client.post(
                "/providers", json={"agent_id": UNKNOWN_AGENT_ID, "display_name": "ghost"}
            )
            assert resp.status_code == 422
            assert resp.json()["code"] == "identity_not_found"
