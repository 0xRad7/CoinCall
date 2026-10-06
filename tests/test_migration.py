"""unit（10 §1）：旧库迁移——起服务自动补 consumer_wallet / latency_ms 列。

场景覆盖：
- 旧库缺 consumer_wallet 且缺 latency_ms（假设的前决策层库）→ 自动 ADD COLUMN，
  旧行可读、新行可写；
- 旧库 latency_ms 为 INTEGER（W3 DDL）→ 自动 SET TYPE BIGINT（10 冻结契约口径）；
- 新库（现行 DDL）→ 迁移幂等 no-op。

参照 core db.py claim_wallet 的 information_schema 模式（A4 只加不改签名）。
"""

import duckdb
import pytest

from app.modules.calls import SETTLE_QUEUE_DDL, CallRecord, CallStatus, CallStore

pytestmark = pytest.mark.unit

#: 前决策层假想旧库（无 consumer_wallet / 无 latency_ms）
LEGACY_CALLS_DDL = """
CREATE TABLE calls (
  call_id             VARCHAR PRIMARY KEY,
  idempotency_key     VARCHAR,
  service_id          VARCHAR,
  provider_agent_id   BIGINT,
  consumer_key_id     VARCHAR,
  amount_raw          BIGINT,
  status              VARCHAR,
  http_status         INTEGER,
  result_hash         VARCHAR,
  payment_nonce       VARCHAR,
  created_at          TIMESTAMP DEFAULT now()
)
"""

#: W3 时期 DDL（consumer_wallet 已有，latency_ms 是 INTEGER）
W3_CALLS_DDL = LEGACY_CALLS_DDL.replace(
    "http_status         INTEGER,",
    "http_status         INTEGER,\n  consumer_wallet     VARCHAR,\n  latency_ms          INTEGER,",
)


def _columns(conn: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?",
        [table],
    ).fetchall()
    return {str(name): str(dtype) for name, dtype in rows}


def _make_legacy_db(path: str, calls_ddl: str) -> None:
    conn = duckdb.connect(path)
    conn.execute(calls_ddl)
    conn.execute(SETTLE_QUEUE_DDL)
    conn.execute(
        "INSERT INTO calls (call_id, service_id, provider_agent_id, consumer_key_id, "
        "amount_raw, status) VALUES ('old1', 'svc_x', 137, 'key_old', 100, 'success')"
    )
    conn.close()


def test_legacy_db_without_columns_gets_them(tmp_path: object) -> None:
    db = str(tmp_path / "gw.duckdb")  # type: ignore[arg-type]
    _make_legacy_db(db, LEGACY_CALLS_DDL)

    store = CallStore(db)
    cols = _columns(store.conn, "calls")
    assert cols["consumer_wallet"] == "VARCHAR"
    assert cols["latency_ms"] == "BIGINT"
    # 旧行保留、缺列字段为 None
    old = store.get_call("old1")
    assert old is not None and old["consumer_wallet"] is None
    assert old["latency_ms"] is None
    # 新行按现行口径可写（含 consumer_wallet + latency_ms）
    store.insert_call(
        CallRecord(
            call_id="new1",
            service_id="svc_x",
            provider_agent_id=137,
            consumer_key_id="key_new",
            consumer_wallet="0x9858EfFD232B4033E47d90003D41EC34EcaEda94",
            amount_raw=100,
        )
    )
    store.mark_call("new1", CallStatus.SUCCESS, http_status=200, latency_ms=42)
    new = store.get_call("new1")
    assert new is not None
    assert new["consumer_wallet"] == "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
    assert new["latency_ms"] == 42
    store.close()


def test_w3_db_latency_int_widened_to_bigint(tmp_path: object) -> None:
    db = str(tmp_path / "gw.duckdb")  # type: ignore[arg-type]
    _make_legacy_db(db, W3_CALLS_DDL)

    store = CallStore(db)
    cols = _columns(store.conn, "calls")
    assert cols["latency_ms"] == "BIGINT"
    assert cols["consumer_wallet"] == "VARCHAR"
    store.close()


def test_migration_idempotent_on_current_ddl(tmp_path: object) -> None:
    db = str(tmp_path / "gw.duckdb")  # type: ignore[arg-type]
    store = CallStore(db)
    store.close()
    again = CallStore(db)  # 二次起库：迁移必须 no-op 不抛
    cols = _columns(again.conn, "calls")
    assert cols["latency_ms"] == "BIGINT"
    assert "consumer_wallet" in cols
    again.close()
