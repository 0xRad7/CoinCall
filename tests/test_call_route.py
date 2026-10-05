"""02 节 7 步时序端到端（全 mock：auth/manifest/chain/provider 均为 fake，零网络）。"""

import base64
import json

import pytest

from app.modules.providers import ProviderError
from tests.conftest import (
    API_KEY,
    CONSUMER_PRIVATE_KEY,
    CONSUMER_WALLET,
    VAULT,
    FakeAuth,
    FakeChain,
    FakeManifests,
    FixedProvider,
    call_headers,
    gateway_serve,
    make_manifest,
    make_x_payment_header,
)

pytestmark = pytest.mark.unit


async def test_healthz(settings: object) -> None:
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "coincall-gateway"}


async def test_success_path_charges_and_enqueues_settle(settings: object) -> None:
    async with gateway_serve(settings) as (client, app):  # type: ignore[arg-type]
        payment = make_x_payment_header(nonce="0x" + "99" * 32)
        resp = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=payment),
            json={"text": "hello"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["result"] == "translated"
        assert resp.headers["X-Charged-Raw"] == "10000"
        receipt_id = resp.headers["X-Receipt-Id"]
        assert receipt_id.startswith("rcp_")
        assert len(resp.headers["X-Receipt-Sig"]) == 64  # HMAC-SHA256 hex

        store = app.state.store
        calls = store.conn.execute("SELECT * FROM calls").fetchall()
        assert len(calls) == 1
        row = store.get_call(store.conn.execute("SELECT call_id FROM calls").fetchone()[0])
        assert row is not None
        assert row["status"] == "success"
        assert row["consumer_wallet"] == CONSUMER_WALLET
        assert row["consumer_key_id"] == "key_unit1"
        assert row["payment_nonce"] == "0x" + "99" * 32
        assert row["result_hash"].startswith("sha256:")

        pending = store.pending_settles(limit=10)
        assert len(pending) == 1
        auth = json.loads(pending[0]["auth_json"])
        assert auth["from"] == CONSUMER_WALLET
        assert auth["v"] == 27 or auth["v"] == 28


async def test_missing_api_key_402_challenge_format(settings: object) -> None:
    """02 §3：无 api key 访问付费服务 → 402 质询（字段逐项断言）。"""
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1",
            headers={"X-PAYMENT": make_x_payment_header(nonce="0x" + "01" * 32)},
            json={"text": "hi"},
        )
        assert resp.status_code == 402
        body = resp.json()
        assert body["error"] == "payment_required"
        assert body["code"] == "missing_api_key"
        assert body["service_id"] == "svc_translate_v1"
        assert body["pricing"] == {"amount": "0.01", "amount_raw": "10000", "token": "USDT"}
        assert "wallet_balance_raw" in body
        assert body["topup"]["endpoint"] == "POST /consumer/topup"
        assert body["topup"]["token"] == "USDT"
        assert body["topup"]["deposit_address"].startswith("0x")
        assert "min_amount" in body["topup"]
        assert body["trace_id"]


async def test_invalid_api_key_401(settings: object) -> None:
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(
                api_key="cck_wrong", payment=make_x_payment_header(nonce="0x" + "02" * 32)
            ),
            json={"text": "hi"},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "apikey_unknown"


async def test_unknown_service_404(settings: object) -> None:
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_nope",
            headers=call_headers(),
            json={"text": "hi"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "service_not_found"


async def test_paused_service_404(settings: object) -> None:
    paused = make_manifest("svc_paused", status="paused")
    async with gateway_serve(
        settings,  # type: ignore[arg-type]
        manifests=FakeManifests({"svc_paused": paused}),
    ) as (client, _app):
        resp = await client.post(
            "/call/svc_paused",
            headers={"X-Api-Key": API_KEY},
            json={"text": "hi"},
        )
        assert resp.status_code == 404


async def test_schema_invalid_422_no_billing(settings: object) -> None:
    async with gateway_serve(settings) as (client, app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=make_x_payment_header(nonce="0x" + "03" * 32)),
            json={
                "wrong_field": 1
            },  # input_schema 要求 {"text": string}（非严格 additionalProperties）
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "request_schema_invalid"
        assert app.state.store.conn.execute("SELECT count(*) FROM calls").fetchone()[0] == 0


async def test_missing_payment_402(settings: object) -> None:
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1",
            headers={"X-Api-Key": API_KEY},
            json={"text": "hi"},
        )
        assert resp.status_code == 402
        assert resp.json()["code"] == "missing_x_payment"


async def test_bad_signature_402(settings: object) -> None:
    other_key_header = make_x_payment_header(
        nonce="0x" + "04" * 32,
        signer=CONSUMER_PRIVATE_KEY,
        from_addr="0x1111111111111111111111111111111111111111",  # 声称的 from ≠ 签名者
    )
    async with gateway_serve(settings) as (client, app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=other_key_header),
            json={"text": "hi"},
        )
        assert resp.status_code == 402
        assert resp.json()["code"] == "signature_mismatch"
        assert app.state.store.conn.execute("SELECT count(*) FROM calls").fetchone()[0] == 0


async def test_payment_expired_402(settings: object) -> None:
    header = make_x_payment_header(valid_before=1, nonce="0x" + "05" * 32)
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1", headers=call_headers(payment=header), json={"text": "hi"}
        )
        assert resp.status_code == 402
        assert resp.json()["code"] == "payment_expired"


async def test_payment_not_yet_valid_402(settings: object) -> None:
    header = make_x_payment_header(valid_after=4_000_000_000, nonce="0x" + "06" * 32)
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1", headers=call_headers(payment=header), json={"text": "hi"}
        )
        assert resp.status_code == 402
        assert resp.json()["code"] == "payment_not_yet_valid"


async def test_amount_mismatch_402(settings: object) -> None:
    header = make_x_payment_header(value="999", nonce="0x" + "07" * 32)
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1", headers=call_headers(payment=header), json={"text": "hi"}
        )
        assert resp.status_code == 402
        assert resp.json()["code"] == "amount_mismatch"


async def test_payee_mismatch_402(settings: object) -> None:
    header = make_x_payment_header(
        to="0x0000000000000000000000000000000000000009", nonce="0x" + "08" * 32
    )
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1", headers=call_headers(payment=header), json={"text": "hi"}
        )
        assert resp.status_code == 402
        assert resp.json()["code"] == "payee_mismatch"


async def test_malformed_payment_402(settings: object) -> None:
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1",
            headers={"X-Api-Key": API_KEY, "X-PAYMENT": "%%%"},
            json={"text": "hi"},
        )
        assert resp.status_code == 402
        assert resp.json()["code"] == "bad_xpayment"


async def test_nonce_replay_402(settings: object) -> None:
    nonce = "0x" + "0a" * 32
    header = make_x_payment_header(nonce=nonce)
    async with gateway_serve(settings) as (client, app):  # type: ignore[arg-type]
        first = await client.post(
            "/call/svc_translate_v1", headers=call_headers(payment=header), json={"text": "one"}
        )
        assert first.status_code == 200
        second = await client.post(
            "/call/svc_translate_v1", headers=call_headers(payment=header), json={"text": "two"}
        )
        assert second.status_code == 402
        assert second.json()["code"] == "nonce_replayed"
        # 只产生一笔流水、一条 settle
        assert app.state.store.conn.execute("SELECT count(*) FROM calls").fetchone()[0] == 1


async def test_chain_insufficient_allowance_402(settings: object) -> None:
    async with gateway_serve(
        settings,
        chain=FakeChain(balance=10**9, allowance=5_000),  # type: ignore[arg-type]
    ) as (client, app):
        resp = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=make_x_payment_header(nonce="0x" + "0b" * 32)),
            json={"text": "hi"},
        )
        assert resp.status_code == 402
        assert resp.json()["code"] == "insufficient_allowance"
        assert resp.json()["pricing"]["amount_raw"] == "10000"
        assert app.state.store.conn.execute("SELECT count(*) FROM calls").fetchone()[0] == 0


async def test_chain_insufficient_balance_402(settings: object) -> None:
    async with gateway_serve(
        settings,
        chain=FakeChain(balance=1_000, allowance=10**9),  # type: ignore[arg-type]
    ) as (client, _app):
        resp = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=make_x_payment_header(nonce="0x" + "0c" * 32)),
            json={"text": "hi"},
        )
        assert resp.status_code == 402
        assert resp.json()["code"] == "insufficient_balance"
        assert resp.json()["wallet_balance_raw"] == "1000"


async def test_shadow_gate_k_exhausted_402(settings: object) -> None:
    async with gateway_serve(settings) as (client, app):  # type: ignore[arg-type]
        gate = app.state.shadow_gate
        for _ in range(3):  # 占满 K=3
            assert gate.try_acquire("key_unit1", 10_000, limit=10**9).allowed
        resp = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=make_x_payment_header(nonce="0x" + "0d" * 32)),
            json={"text": "hi"},
        )
        assert resp.status_code == 402
        assert resp.json()["code"] == "shadow_k_exceeded"


async def test_idempotency_replay_same_body(settings: object) -> None:
    header = make_x_payment_header(nonce="0x" + "0e" * 32)
    async with gateway_serve(settings) as (client, app):  # type: ignore[arg-type]
        first = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=header, idempotency="idem-1"),
            json={"text": "same"},
        )
        second = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=header, idempotency="idem-1"),
            json={"text": "same"},
        )
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert first.headers["X-Receipt-Id"] == second.headers["X-Receipt-Id"]
        # 幂等重放不双计：一条流水一条 settle
        assert app.state.store.conn.execute("SELECT count(*) FROM calls").fetchone()[0] == 1
        assert len(app.state.store.pending_settles(limit=10)) == 1


async def test_idempotency_conflict_different_body(settings: object) -> None:
    header = make_x_payment_header(nonce="0x" + "0f" * 32)
    async with gateway_serve(settings) as (client, _app):  # type: ignore[arg-type]
        first = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=header, idempotency="idem-2"),
            json={"text": "one"},
        )
        assert first.status_code == 200
        second = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(
                payment=make_x_payment_header(nonce="0x" + "10" * 32), idempotency="idem-2"
            ),
            json={"text": "different"},
        )
        assert second.status_code == 409
        assert second.json()["code"] == "idempotency_conflict"


async def test_provider_failure_aborts_without_settle(settings: object) -> None:
    failing = FixedProvider(error=ProviderError("provider 500"))
    async with gateway_serve(
        settings,
        providers={"internal": failing, "http_json": failing},  # type: ignore[arg-type]
    ) as (client, app):
        resp = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=make_x_payment_header(nonce="0x" + "11" * 32)),
            json={"text": "hi"},
        )
        assert resp.status_code == 502
        body = resp.json()
        assert body["error"] == "service_error"
        assert body["code"] == "provider_failed"
        store = app.state.store
        assert store.conn.execute("SELECT count(*) FROM settle_queue").fetchone()[0] == 0
        row = store.conn.execute("SELECT status FROM calls").fetchone()
        assert row is not None and row[0] == "aborted"
        # 闸门释放：后续调用可再进
        followup = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=make_x_payment_header(nonce="0x" + "12" * 32)),
            json={"text": "hi"},
        )
        assert followup.status_code == 502


async def test_http_json_provider_route(settings: object) -> None:
    """endpoint.type=http_json 走注入的 provider（真实 httpx 转发属 W4 接线）。"""
    external = make_manifest(
        "svc_external",
        endpoint={"type": "http_json", "url": "https://p.example/x", "timeout_ms": 5000},
    )
    provider = FixedProvider(body={"ok": True})
    async with gateway_serve(
        settings,  # type: ignore[arg-type]
        manifests=FakeManifests({"svc_external": external}),
        providers={"internal": FixedProvider(), "http_json": provider},
    ) as (client, _app):
        resp = await client.post(
            "/call/svc_external",
            headers=call_headers(payment=make_x_payment_header(nonce="0x" + "13" * 32)),
            json={"text": "hi"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True}
        assert provider.forwarded[0]["body"] == {"text": "hi"}


async def test_auth_error_maps_401(settings: object) -> None:
    async with gateway_serve(
        settings,
        auth=FakeAuth(keys_by_key={}),  # type: ignore[arg-type]
    ) as (client, _app):
        resp = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=make_x_payment_header(nonce="0x" + "14" * 32)),
            json={"text": "hi"},
        )
        assert resp.status_code == 401


async def test_quota_exceeded_402(settings: object) -> None:
    from app.modules.auth import ApiKeyInfo

    quota_auth = FakeAuth(
        keys_by_key={
            API_KEY: ApiKeyInfo(
                key_id="key_unit1",
                consumer_wallet=CONSUMER_WALLET,
                quota_raw=5_000,
                status="active",
            )
        }
    )
    async with gateway_serve(settings, auth=quota_auth) as (client, _app):  # type: ignore[arg-type]
        resp = await client.post(
            "/call/svc_translate_v1",
            headers=call_headers(payment=make_x_payment_header(nonce="0x" + "15" * 32)),
            json={"text": "hi"},
        )
        assert resp.status_code == 402
        assert resp.json()["code"] == "quota_exceeded"


def test_x_payment_header_roundtrip_util() -> None:
    """夹具自检：make_x_payment_header 产出的头可被 base64 解码。"""
    header = make_x_payment_header()
    payload = json.loads(base64.b64decode(header))
    assert set(payload) == {
        "from",
        "to",
        "value",
        "validAfter",
        "validBefore",
        "nonce",
        "v",
        "r",
        "s",
    }
    assert payload["from"] == CONSUMER_WALLET
    assert payload["to"] == VAULT
