"""冻结契约 #4：calls / settle_queue 表模型 + DDL + CallStore（02 §5，字段冻结）。

> 契约冻结（08 §2）：status 枚举含 settled/bad_debt；04 keeper 与 06 排行
> 按此表读，02 网关只写。

settle_queue.auth_json 内嵌完整授权（六元组 + v/r/s），供 keeper 组装
PayVault.chargeWithSigBatch calldata（"provider"=provider_token_id 列）。
"""

import json
import threading
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, Field

#: 02 §5 calls 表（status: inflight|success|aborted|settled|bad_debt）
CALLS_DDL = """
CREATE TABLE IF NOT EXISTS calls (
  call_id             VARCHAR PRIMARY KEY,
  idempotency_key     VARCHAR,
  service_id          VARCHAR,
  provider_agent_id   BIGINT,
  consumer_key_id     VARCHAR,
  consumer_wallet     VARCHAR,
  amount_raw          BIGINT,
  status              VARCHAR,
  http_status         INTEGER,
  latency_ms          INTEGER,
  result_hash         VARCHAR,
  payment_nonce       VARCHAR,
  created_at          TIMESTAMP DEFAULT now()
)
"""

#: 02 §5 settle 队列（keeper 消费，02 只写；status: pending|done|failed|expired）
SETTLE_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS settle_queue (
  call_id            VARCHAR PRIMARY KEY,
  provider_token_id  BIGINT,
  auth_json          JSON,
  status             VARCHAR DEFAULT 'pending',
  created_at         TIMESTAMP DEFAULT now()
)
"""


class CallStatus(StrEnum):
    INFLIGHT = "inflight"
    SUCCESS = "success"
    ABORTED = "aborted"
    SETTLED = "settled"
    BAD_DEBT = "bad_debt"


class SettleStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"  # keeper 过期作废（09 P0-5）


class CallRecord(BaseModel):
    """calls 行模型（写入口径）。"""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    idempotency_key: str | None = None
    service_id: str
    provider_agent_id: int
    consumer_key_id: str
    consumer_wallet: str
    amount_raw: int
    status: CallStatus = CallStatus.INFLIGHT
    payment_nonce: str | None = None


class SettleRecord(BaseModel):
    """settle_queue 行模型（读取口径；auth_json 含 from/to/value/v/r/s…）。"""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    provider_token_id: int
    auth: dict[str, Any] = Field(description="完整授权 JSON（六元组 + v/r/s）")
    status: SettleStatus = SettleStatus.PENDING


class CallStore:
    """gateway 自有 DuckDB（C-14 单写者：单连接 + 锁）。"""

    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(path)
        self._lock = threading.Lock()
        with self._lock:
            self.conn.execute(CALLS_DDL)
            self.conn.execute(SETTLE_QUEUE_DDL)

    # ---- calls ----

    def insert_call(self, record: CallRecord) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO calls (call_id, idempotency_key, service_id, provider_agent_id, "
                "consumer_key_id, consumer_wallet, amount_raw, status, payment_nonce) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    record.call_id,
                    record.idempotency_key,
                    record.service_id,
                    record.provider_agent_id,
                    record.consumer_key_id,
                    record.consumer_wallet,
                    record.amount_raw,
                    record.status.value,
                    record.payment_nonce,
                ],
            )

    def mark_call(
        self,
        call_id: str,
        status: CallStatus,
        http_status: int | None = None,
        latency_ms: int | None = None,
        result_hash: str | None = None,
    ) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE calls SET status = ?, "
                "http_status = coalesce(?, http_status), "
                "latency_ms = coalesce(?, latency_ms), "
                "result_hash = coalesce(?, result_hash) WHERE call_id = ?",
                [status.value, http_status, latency_ms, result_hash, call_id],
            )

    def get_call(self, call_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT call_id, idempotency_key, service_id, provider_agent_id, consumer_key_id, "
            "consumer_wallet, amount_raw, status, http_status, latency_ms, result_hash, "
            "payment_nonce, created_at FROM calls WHERE call_id = ?",
            [call_id],
        ).fetchone()
        if row is None:
            return None
        return {
            "call_id": row[0],
            "idempotency_key": row[1],
            "service_id": row[2],
            "provider_agent_id": row[3],
            "consumer_key_id": row[4],
            "consumer_wallet": row[5],
            "amount_raw": row[6],
            "status": row[7],
            "http_status": row[8],
            "latency_ms": row[9],
            "result_hash": row[10],
            "payment_nonce": row[11],
            "created_at": str(row[12]),
        }

    def find_by_idempotency(self, idempotency_key: str, service_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT call_id FROM calls WHERE idempotency_key = ? AND service_id = ? LIMIT 1",
            [idempotency_key, service_id],
        ).fetchone()
        return self.get_call(row[0]) if row is not None else None

    # ---- settle_queue ----

    def insert_settle(self, *, call_id: str, provider_token_id: int, auth: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO settle_queue (call_id, provider_token_id, auth_json) "
                "VALUES (?, ?, CAST(? AS JSON))",
                [call_id, provider_token_id, json.dumps(auth, sort_keys=True)],
            )

    def pending_settles(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT call_id, provider_token_id, auth_json, status, created_at "
            "FROM settle_queue WHERE status = 'pending' ORDER BY created_at LIMIT ?",
            [limit],
        ).fetchall()
        return [
            {
                "call_id": r[0],
                "provider_token_id": r[1],
                "auth_json": r[2],
                "status": r[3],
                "created_at": str(r[4]),
            }
            for r in rows
        ]

    def mark_settle(self, call_id: str, status: SettleStatus) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE settle_queue SET status = ? WHERE call_id = ?",
                [status.value, call_id],
            )

    def close(self) -> None:
        with self._lock:
            self.conn.close()
