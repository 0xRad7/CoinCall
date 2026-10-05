"""T10：01/03 节 api key 签发与校验（明文只回显一次，落库只存 hash）。"""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

WALLET = "0x1234567890AbCdEf1234567890aBcDeF12345678"


def test_issue_returns_plaintext_once_and_stores_hash(client: TestClient) -> None:
    resp = client.post("/apikeys", json={"consumer_wallet": WALLET, "quota_raw": 1000000})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["consumer_wallet"] == WALLET
    api_key = body["api_key"]
    assert isinstance(api_key, str) and api_key.startswith("cck_")

    # 落库只有 hash：store 里查不到明文
    store = client.app.state.store
    rows = store.conn.execute("SELECT key_hash FROM api_keys").fetchall()
    assert rows and all(api_key != r[0] for r in rows)


def test_validate_roundtrip_returns_wallet_and_quota(client: TestClient) -> None:
    issue = client.post("/apikeys", json={"consumer_wallet": WALLET}).json()
    resp = client.post("/internal/apikeys/validate", json={"api_key": issue["api_key"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key_id"] == issue["key_id"]
    assert body["consumer_wallet"] == WALLET
    assert "status" in body and body["status"] == "active"


def test_validate_unknown_key_401(client: TestClient) -> None:
    resp = client.post("/internal/apikeys/validate", json={"api_key": "cck_deadbeef"})
    assert resp.status_code == 401
    for field in ("error", "detail", "code", "trace_id"):
        assert field in resp.json()


def test_validate_revoked_key_401(client: TestClient) -> None:
    issue = client.post("/apikeys", json={"consumer_wallet": WALLET}).json()
    client.app.state.store.set_api_key_status(issue["key_id"], "revoked")
    resp = client.post("/internal/apikeys/validate", json={"api_key": issue["api_key"]})
    assert resp.status_code == 401
    assert resp.json()["code"] == "apikey_revoked"


def test_issue_rejects_bad_wallet(client: TestClient) -> None:
    resp = client.post("/apikeys", json={"consumer_wallet": "not-an-address"})
    assert resp.status_code == 422


def test_two_keys_same_wallet_distinct_ids(client: TestClient) -> None:
    a = client.post("/apikeys", json={"consumer_wallet": WALLET}).json()
    b = client.post("/apikeys", json={"consumer_wallet": WALLET}).json()
    assert a["key_id"] != b["key_id"]
    assert a["api_key"] != b["api_key"]
