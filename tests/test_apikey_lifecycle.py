"""T20：03 §2 api key 生命周期——列表（无明文）/吊销立即生效/换绑钱包（P1-2）。"""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

WALLET = "0x1234567890AbCdEf1234567890aBcDeF12345678"
WALLET_B = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"


def _issue(client: TestClient, wallet: str = WALLET) -> dict:
    resp = client.post("/apikeys", json={"consumer_wallet": wallet})
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestApiKeyList:
    def test_list_returns_keys_without_plaintext_or_hash(self, client: TestClient) -> None:
        a = _issue(client)
        b = _issue(client)
        resp = client.get("/apikeys")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = {row["key_id"] for row in body["keys"]}
        assert ids == {a["key_id"], b["key_id"]}
        raw = resp.text
        assert a["api_key"] not in raw  # 明文绝不出现
        for row in body["keys"]:
            assert set(row) == {"key_id", "consumer_wallet", "quota_raw", "status", "created_at"}

    def test_list_empty_graceful(self, client: TestClient) -> None:
        resp = client.get("/apikeys")
        assert resp.status_code == 200
        assert resp.json()["keys"] == []


class TestApiKeyRevoke:
    def test_delete_revokes_and_validate_rejects(self, client: TestClient) -> None:
        issue = _issue(client)
        resp = client.delete(f"/apikeys/{issue['key_id']}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "revoked"
        # 吊销立即生效：validate → 401 apikey_revoked（网关侧即拒）
        check = client.post("/internal/apikeys/validate", json={"api_key": issue["api_key"]})
        assert check.status_code == 401
        assert check.json()["code"] == "apikey_revoked"
        # 列表可见 revoked 状态
        listed = {r["key_id"]: r for r in client.get("/apikeys").json()["keys"]}
        assert listed[issue["key_id"]]["status"] == "revoked"

    def test_delete_unknown_404(self, client: TestClient) -> None:
        resp = client.delete("/apikeys/key_nope")
        assert resp.status_code == 404
        assert resp.json()["code"] == "apikey_not_found"

    def test_delete_idempotent_returns_revoked(self, client: TestClient) -> None:
        issue = _issue(client)
        assert client.delete(f"/apikeys/{issue['key_id']}").status_code == 200
        again = client.delete(f"/apikeys/{issue['key_id']}")
        assert again.status_code == 200
        assert again.json()["status"] == "revoked"


class TestApiKeyRebind:
    def test_put_wallet_rebinds(self, client: TestClient) -> None:
        issue = _issue(client)
        resp = client.put(f"/apikeys/{issue['key_id']}/wallet", json={"consumer_wallet": WALLET_B})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["key_id"] == issue["key_id"]
        assert body["consumer_wallet"] == WALLET_B
        # 换绑后 validate 返回新钱包（X-PAYMENT.from 必须等于它）
        check = client.post("/internal/apikeys/validate", json={"api_key": issue["api_key"]})
        assert check.status_code == 200
        assert check.json()["consumer_wallet"] == WALLET_B

    def test_put_wallet_unknown_404(self, client: TestClient) -> None:
        resp = client.put("/apikeys/key_nope/wallet", json={"consumer_wallet": WALLET_B})
        assert resp.status_code == 404

    def test_put_wallet_bad_address_422(self, client: TestClient) -> None:
        issue = _issue(client)
        resp = client.put(
            f"/apikeys/{issue['key_id']}/wallet", json={"consumer_wallet": "0xdeadbeef"}
        )
        assert resp.status_code == 422

    def test_put_wallet_revoked_key_still_rebinds_but_validate_rejects(
        self, client: TestClient
    ) -> None:
        issue = _issue(client)
        client.delete(f"/apikeys/{issue['key_id']}")
        resp = client.put(f"/apikeys/{issue['key_id']}/wallet", json={"consumer_wallet": WALLET_B})
        assert resp.status_code == 200
        check = client.post("/internal/apikeys/validate", json={"api_key": issue["api_key"]})
        assert check.status_code == 401  # 吊销态不因换绑复活
