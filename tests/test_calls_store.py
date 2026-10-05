"""T13：02 §5 calls / settle_queue DDL 与状态机（字段冻结）。"""

import json

import duckdb
import pytest
from app.modules.calls import (
    CALLS_DDL,
    SETTLE_QUEUE_DDL,
    CallRecord,
    CallStatus,
    CallStore,
    SettleStatus,
)

pytestmark = pytest.mark.unit


@pytest.fixture()
def store(tmp_path: object) -> CallStore:
    return CallStore(str(tmp_path / "gw.duckdb"))  # type: ignore[arg-type]


def sample_call(call_id: str = "call_001", **overrides: object) -> CallRecord:
    fields: dict[str, object] = {
        "call_id": call_id,
        "idempotency_key": "idem-1",
        "service_id": "svc_translate_v1",
        "provider_agent_id": 137,
        "consumer_key_id": "key_unit1",
        "consumer_wallet": "0x9858EfFD232B4033E47d90003D41EC34EcaEda94",
        "amount_raw": 10000,
        "status": CallStatus.INFLIGHT,
        "payment_nonce": "0x" + "00" * 31 + "01",
    }
    fields.update(overrides)
    return CallRecord(**fields)  # type: ignore[arg-type]


def test_ddl_freezes_columns(store: CallStore) -> None:
    """02 §5 字段逐列冻结（status 枚举含 settled/bad_debt）。"""
    conn: duckdb.DuckDBPyConnection = store.conn
    calls_cols = {r[1] for r in conn.execute("PRAGMA table_info('calls')").fetchall()}
    assert calls_cols == {
        "call_id",
        "idempotency_key",
        "service_id",
        "provider_agent_id",
        "consumer_key_id",
        "consumer_wallet",
        "amount_raw",
        "status",
        "http_status",
        "latency_ms",
        "result_hash",
        "payment_nonce",
        "created_at",
    }
    queue_cols = {r[1] for r in conn.execute("PRAGMA table_info('settle_queue')").fetchall()}
    assert queue_cols == {
        "call_id",
        "provider_token_id",
        "auth_json",
        "status",
        "created_at",
    }
    # DDL 常量可独立执行（幂等）
    conn.execute(
        CALLS_DDL.replace(
            "CREATE TABLE IF NOT EXISTS calls", "CREATE TABLE IF NOT EXISTS calls_copy"
        )
    )
    conn.execute(
        SETTLE_QUEUE_DDL.replace(
            "CREATE TABLE IF NOT EXISTS settle_queue",
            "CREATE TABLE IF NOT EXISTS settle_queue_copy",
        )
    )


def test_status_enum_covers_contract() -> None:
    assert {s.value for s in CallStatus} == {
        "inflight",
        "success",
        "aborted",
        "settled",
        "bad_debt",
    }
    assert {s.value for s in SettleStatus} >= {"pending", "done", "failed"}


def test_insert_and_get_call(store: CallStore) -> None:
    store.insert_call(sample_call())
    got = store.get_call("call_001")
    assert got is not None
    assert got["service_id"] == "svc_translate_v1"
    assert got["status"] == "inflight"
    assert got["amount_raw"] == 10000
    assert got["payment_nonce"] == "0x" + "00" * 31 + "01"


def test_lifecycle_inflight_to_success(store: CallStore) -> None:
    store.insert_call(sample_call())
    store.mark_call(
        "call_001", CallStatus.SUCCESS, http_status=200, latency_ms=42, result_hash="sha256:ab"
    )
    got = store.get_call("call_001")
    assert got is not None
    assert got["status"] == "success"
    assert got["http_status"] == 200
    assert got["latency_ms"] == 42
    assert got["result_hash"] == "sha256:ab"


def test_lifecycle_aborted_and_settled_and_bad_debt(store: CallStore) -> None:
    for status, call_id in (
        (CallStatus.ABORTED, "call_a"),
        (CallStatus.SETTLED, "call_b"),
        (CallStatus.BAD_DEBT, "call_c"),
    ):
        store.insert_call(sample_call(call_id=call_id))
        store.mark_call(call_id, status)
        got = store.get_call(call_id)
        assert got is not None
        assert got["status"] == status.value


def test_find_by_idempotency(store: CallStore) -> None:
    store.insert_call(sample_call("call_1", idempotency_key="idem-x", service_id="svc_a"))
    store.insert_call(sample_call("call_2", idempotency_key="idem-y", service_id="svc_b"))
    hit = store.find_by_idempotency("idem-x", "svc_a")
    assert hit is not None
    assert hit["call_id"] == "call_1"
    assert store.find_by_idempotency("idem-x", "svc_b") is None  # 同 key 不同服务不算重放
    assert store.find_by_idempotency("idem-missing", "svc_a") is None


def test_settle_queue_roundtrip_with_signature(store: CallStore) -> None:
    auth = {
        "from": "0x9858EfFD232B4033E47d90003D41EC34EcaEda94",
        "to": "0x000000000000000000000000000000000000dEaD",
        "value": "10000",
        "validAfter": 0,
        "validBefore": 2000000000,
        "nonce": "0x" + "00" * 31 + "01",
        "v": 27,
        "r": "0x" + "22" * 32,
        "s": "0x" + "33" * 32,
    }
    store.insert_call(sample_call("call_9"))
    store.insert_settle(call_id="call_9", provider_token_id=137, auth=auth)
    pending = store.pending_settles(limit=10)
    assert len(pending) == 1
    row = pending[0]
    assert row["call_id"] == "call_9"
    assert row["provider_token_id"] == 137
    assert row["status"] == "pending"
    restored = json.loads(row["auth_json"])
    assert restored["v"] == 27 and len(restored["r"]) == 66  # v/r/s 完整可回放

    store.mark_settle("call_9", SettleStatus.DONE)
    assert store.pending_settles(limit=10) == []
