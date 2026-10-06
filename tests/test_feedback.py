"""付费反馈权（10 §2）：Ed25519 验签 + 收据核销 + 防自评 oracle + 窗口限频 + 聚合查询。

五路主径：正常 201 / 重复收据 409 / 验签失败 401 / 跨服务收据 422 / 窗口限频 429；
辅助径：非 success 收据 422、未知服务 404、公钥不可得 503（仅反馈面降级）、自评 403（oracle）。
"""

import copy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import (
    VALID_MANIFEST,
    FakeChainClient,
    FakeGatewayStatsClient,
    FakeIdentityClient,
    FakeReceiptPubkeyClient,
    sign_fake_receipt,
)

pytestmark = pytest.mark.unit

PROVIDER_WALLET = "0x1234567890AbCdEf1234567890aBcDeF12345678"
OTHER_SERVICE = "svc_weather_v1"
TS = "2026-10-06T12:00:00+00:00"


class FakePayerOracle:
    """ReceiptPayerSource 桩：可编程逐收据 payer（防自评 403 数据面）。"""

    def __init__(self, payer: str | None) -> None:
        self.payer = payer
        self.asked: list[str] = []

    def payer_of(self, receipt_id: str) -> str | None:
        self.asked.append(receipt_id)
        return self.payer


def _client(
    tmp_path: Path,
    *,
    gateway: FakeGatewayStatsClient | None = None,
    key_unavailable: bool = False,
    payer: FakePayerOracle | None = None,
) -> TestClient:
    settings = Settings(duckdb_path=str(tmp_path / "fb.duckdb"))
    app = create_app(
        settings,
        identity_client=FakeIdentityClient(),
        chain_client=FakeChainClient(),
        gateway_client=gateway or FakeGatewayStatsClient(),
        receipt_key_client=FakeReceiptPubkeyClient(unavailable=key_unavailable),
        feedback_payer=payer,
    )
    return TestClient(app)


def _seed_services(client: TestClient) -> None:
    assert client.post("/manifests", json=VALID_MANIFEST).status_code == 201
    weather = copy.deepcopy(VALID_MANIFEST)
    weather["service_id"] = OTHER_SERVICE
    weather["provider"] = dict(weather["provider"], agent_id=138, wallet="0xee" + "ee" * 19 + "ee")
    assert client.post("/manifests", json=weather).status_code == 201


def _body(
    key: Ed25519PrivateKey,
    *,
    receipt_id: str = "rcp_good_0001",
    receipt_svc: str = "svc_translate_v1",
    target_svc: str | None = None,
    amount_raw: str = "10000",
    status: str = "success",
    ts: str = TS,
    rating: int = 5,
    comment: str | None = None,
    sig_hex: str | None = None,
) -> dict[str, object]:
    sig = sig_hex or sign_fake_receipt(
        key,
        receipt_id=receipt_id,
        service_id=receipt_svc,
        amount_raw=amount_raw,
        status=status,
        ts=ts,
    )
    return {
        "service_id": target_svc if target_svc is not None else receipt_svc,
        "receipt": {
            "receipt_id": receipt_id,
            "service_id": receipt_svc,
            "amount_raw": amount_raw,
            "status": status,
            "ts": ts,
            "receipt_sig_hex": sig,
        },
        "rating": rating,
        "comment": comment,
    }


def _post(client: TestClient, body: dict[str, object]) -> object:
    return client.post("/feedback", json=body)


class TestFeedbackHappyPath:
    def test_submit_and_aggregate(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            _seed_services(client)
            key = client.app.state.receipt_pubkey.key
            resp = _post(client, _body(key, comment="翻译质量很好"))
            assert resp.status_code == 201, resp.text
            assert resp.json() == {
                "service_id": "svc_translate_v1",
                "receipt_id": "rcp_good_0001",
                "rating": 5,
                "verified_paid": True,
                "created_at": resp.json()["created_at"],
            }
            assert resp.json()["verified_paid"] is True

            # 第二条不同收据（无网关统计行=无限频口径）→ 4 分
            resp2 = _post(client, _body(key, receipt_id="rcp_good_0002", rating=4))
            assert resp2.status_code == 201, resp2.text

            agg = client.get("/feedback/services/svc_translate_v1").json()
            assert agg["count"] == 2
            assert agg["avg"] == 4.5
            assert agg["verified_paid"] is True
            assert [e["receipt_id"] for e in agg["entries"]] == ["rcp_good_0001", "rcp_good_0002"]
            assert agg["entries"][0]["comment"] == "翻译质量很好"
            assert all(e["created_at"] for e in agg["entries"])

    def test_unknown_service_404(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            _seed_services(client)
            key = client.app.state.receipt_pubkey.key
            resp = _post(client, _body(key, receipt_svc="svc_ghost_v1"))
            assert resp.status_code == 404
            assert resp.json()["code"] == "service_not_found"
            assert client.get("/feedback/services/svc_ghost_v1").status_code == 404

    def test_validation_bounds(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            _seed_services(client)
            key = client.app.state.receipt_pubkey.key
            assert _post(client, _body(key, rating=0)).status_code == 422
            assert _post(client, _body(key, rating=6)).status_code == 422
            assert _post(client, _body(key, comment="x" * 281)).status_code == 422


class TestFeedbackFivePaths:
    def test_duplicate_receipt_409(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            _seed_services(client)
            key = client.app.state.receipt_pubkey.key
            assert _post(client, _body(key)).status_code == 201
            resp = _post(client, _body(key, rating=3))  # 同收据再提交（评分不同也不行）
            assert resp.status_code == 409
            assert resp.json()["code"] == "receipt_redeemed"
            # 核销不可逆：库里仍只有一条
            assert client.get("/feedback/services/svc_translate_v1").json()["count"] == 1

    def test_bad_signature_401(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            _seed_services(client)
            key = client.app.state.receipt_pubkey.key
            resp = _post(client, _body(key, sig_hex="ab" * 64))  # 签名对不上规范串
            assert resp.status_code == 401
            assert resp.json()["code"] == "receipt_sig_invalid"
            # 换一把私钥签（伪装网关）同样 401
            rogue = FakeReceiptPubkeyClient().key
            resp2 = _post(client, _body(rogue))
            assert resp2.status_code == 401

    def test_cross_service_receipt_422(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            _seed_services(client)
            key = client.app.state.receipt_pubkey.key
            # 收据真实属于 svc_weather_v1（签名合法），却提交给 svc_translate_v1
            resp = _post(client, _body(key, receipt_svc=OTHER_SERVICE))
            assert resp.status_code == 422
            assert resp.json()["code"] == "receipt_service_mismatch"

    def test_window_limit_429(self, tmp_path: Path) -> None:
        """窗口限频（§E 实施级简化）：同窗反馈数 ≥ 网关 distinct_payers 即拒收。"""
        gateway = FakeGatewayStatsClient(
            stats={
                "services": [
                    {
                        "service_id": "svc_translate_v1",
                        "calls_success": 3,
                        "calls_settled": 3,
                        "calls_aborted": 0,
                        "bad_debt": 0,
                        "p50_ms": 500,
                        "p95_ms": 900,
                        "distinct_payers": 1,
                        "last_activity_at": TS,
                        "window_hours": 168,
                    }
                ],
                "totals": {},
            }
        )
        with _client(tmp_path, gateway=gateway) as client:
            _seed_services(client)
            key = client.app.state.receipt_pubkey.key
            assert _post(client, _body(key, receipt_id="rcp_w1")).status_code == 201
            resp = _post(client, _body(key, receipt_id="rcp_w2"))  # 第 2 条 > distinct_payers=1
            assert resp.status_code == 429
            assert resp.json()["code"] == "feedback_window_limit"

    def test_non_success_receipt_422(self, tmp_path: Path) -> None:
        with _client(tmp_path) as client:
            _seed_services(client)
            key = client.app.state.receipt_pubkey.key
            resp = _post(client, _body(key, status="aborted"))
            assert resp.status_code == 422
            assert resp.json()["code"] == "receipt_not_success"


class TestFeedbackDegradationAndSelfRating:
    def test_pubkey_unavailable_503_only_feedback(self, tmp_path: Path) -> None:
        with _client(tmp_path, key_unavailable=True) as client:
            _seed_services(client)
            key = Ed25519PrivateKey.generate()  # 无法验签：任意签名都到不了验签步
            resp = _post(client, _body(key))
            assert resp.status_code == 503
            assert resp.json()["code"] == "receipt_key_unavailable"
            # 降级仅限反馈面：决策/聚合查询不受影响
            assert client.get("/decision/services").status_code == 200
            assert client.get("/feedback/services/svc_translate_v1").status_code == 200

    def test_self_rating_403_via_payer_oracle(self, tmp_path: Path) -> None:
        """防自评（10 §2）：payer==provider.wallet → 403（oracle 数据面就绪即生效）。"""
        payer = FakePayerOracle(payer=PROVIDER_WALLET)
        with _client(tmp_path, payer=payer) as client:
            _seed_services(client)
            key = client.app.state.receipt_pubkey.key
            resp = _post(client, _body(key))
            assert resp.status_code == 403
            assert resp.json()["code"] == "self_rating_blocked"
            assert payer.asked == ["rcp_good_0001"]
            # 他人付款不受影响
            client.app.state.feedback_payer = FakePayerOracle(
                payer="0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
            )
            assert _post(client, _body(key, receipt_id="rcp_other_01")).status_code == 201
