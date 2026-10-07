"""Provider 认领语义（认证先行）：登记=验证链上 agentWallet==认领钱包 + 一身份一认领。"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import FakeIdentityClient

pytestmark = pytest.mark.unit

CUSTODIAN = "0xc37ffe97b4d2c3d0187b1ddedf273e52a461b63a"
MY_WALLET = "0x1111111111111111111111111111111111111111"
OTHER = "0x2222222222222222222222222222222222222222"


def _client(tmp_path, known=None):
    with TestClient(
        create_app(
            Settings(_env_file=None, duckdb_path=str(tmp_path / "core.duckdb")),  # 单元隔离
            identity_client=FakeIdentityClient(known={137: CUSTODIAN} if known is None else known),
        )
    ) as c:
        yield c


def test_register_requires_claim_wallet(tmp_path):
    with next(_client(tmp_path)) as c:
        r = c.post("/providers", json={"agent_id": 137, "display_name": "x"})
        assert r.status_code == 422 and "claim_wallet" in r.text


def test_agentwallet_mismatch_requires_binding(tmp_path):
    with next(_client(tmp_path)) as c:
        r = c.post(
            "/providers", json={"agent_id": 137, "display_name": "x", "claim_wallet": MY_WALLET}
        )
        assert r.status_code == 422 and "claim_requires_binding" in r.text


def test_claim_success_when_agentwallet_matches(tmp_path):
    with next(_client(tmp_path, known={137: MY_WALLET})) as c:
        r = c.post(
            "/providers", json={"agent_id": 137, "display_name": "me", "claim_wallet": MY_WALLET}
        )
        assert r.status_code == 201, r.text
        assert r.json()["claim_wallet"] == MY_WALLET
        # 同钱包重复认领=改名，幂等
        r2 = c.post(
            "/providers", json={"agent_id": 137, "display_name": "me2", "claim_wallet": MY_WALLET}
        )
        assert r2.status_code in (200, 201) and r2.json()["display_name"] == "me2"


def test_first_claim_wins_409(tmp_path):
    with next(_client(tmp_path, known={137: MY_WALLET})) as c:
        c.post(
            "/providers", json={"agent_id": 137, "display_name": "me", "claim_wallet": MY_WALLET}
        )
        r = c.post(
            "/providers", json={"agent_id": 137, "display_name": "evil", "claim_wallet": OTHER}
        )
        assert r.status_code == 409 and "identity_already_claimed" in r.text


def test_claim_state_endpoint(tmp_path):
    with next(_client(tmp_path)) as c:
        r = c.get("/providers/137/claim-state")
        assert r.status_code == 200
        body = r.json()
        assert body["identity_found"] is True
        assert body["agent_wallet"] == CUSTODIAN
        assert body["claimed_by_wallet"] is None
        assert body["platform_custodian"] == CUSTODIAN
        c.post(
            "/providers", json={"agent_id": 137, "display_name": "me", "claim_wallet": CUSTODIAN}
        )
        body = c.get("/providers/137/claim-state").json()
        assert body["claimed_by_wallet"] == CUSTODIAN


def test_claim_state_identity_missing(tmp_path):
    with next(_client(tmp_path)) as c:
        body = c.get("/providers/999/claim-state").json()
        assert body["identity_found"] is False


def test_register_bypasses_identity_cache(tmp_path):
    """绑定后立即登记：identity 客户端必须拿新鲜值（不命中 60s 缓存）。"""
    fake = FakeIdentityClient(known={137: MY_WALLET})
    fake.calls.clear()
    with TestClient(
        create_app(
            Settings(_env_file=None, duckdb_path=str(tmp_path / "core.duckdb")),  # 单元隔离
            identity_client=fake,
        )
    ) as c:
        fake.known[137] = CUSTODIAN  # 先以旧值预热缓存
        c.get("/providers/137/claim-state")
        assert fake.calls  # 预热成功
        fake.known[137] = MY_WALLET  # 模拟绑定完成
        r = c.post(
            "/providers", json={"agent_id": 137, "display_name": "me", "claim_wallet": MY_WALLET}
        )
        assert r.status_code == 201, r.text
