"""DuckDB 存储（单写者纪律 C-14）：单连接 + 进程内互斥锁。"""

import json
import threading
from pathlib import Path
from typing import Any

import duckdb

SERVICES_DDL = """
CREATE TABLE IF NOT EXISTS services (
  service_id    VARCHAR PRIMARY KEY,
  manifest      JSON,
  status        VARCHAR DEFAULT 'active',
  manifest_hash VARCHAR,
  created_at    TIMESTAMP DEFAULT now(),
  updated_at    TIMESTAMP DEFAULT now()
)
"""

API_KEYS_DDL = """
CREATE TABLE IF NOT EXISTS api_keys (
  key_id          VARCHAR PRIMARY KEY,
  key_hash        VARCHAR UNIQUE,
  consumer_wallet VARCHAR,
  quota_raw       BIGINT,
  status          VARCHAR DEFAULT 'active',
  created_at      TIMESTAMP DEFAULT now()
)
"""


class CoreStore:
    """coincall-core 自有 DuckDB 库。

    约束：一个库文件同一时刻只被一个进程持有（C-14）；
    进程内单连接 + threading.Lock 串行化写（单写者纪律）。
    """

    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(path)
        self._lock = threading.Lock()
        with self._lock:
            self.conn.execute(SERVICES_DDL)
            self.conn.execute(API_KEYS_DDL)

    # ---- services / manifests ----

    def upsert_service(
        self, service_id: str, manifest: dict[str, Any], status: str, manifest_hash: str
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO services (service_id, manifest, status, manifest_hash)
                VALUES (?, CAST(? AS JSON), ?, ?)
                ON CONFLICT (service_id) DO UPDATE SET
                  manifest = CAST(? AS JSON),
                  status = excluded.status,
                  manifest_hash = excluded.manifest_hash,
                  updated_at = now()
                """,
                [service_id, json.dumps(manifest), status, manifest_hash, json.dumps(manifest)],
            )

    def get_service(self, service_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT service_id, manifest, status, manifest_hash, created_at, updated_at "
            "FROM services WHERE service_id = ?",
            [service_id],
        ).fetchone()
        if row is None:
            return None
        return {
            "service_id": row[0],
            "manifest": json.loads(row[1]),
            "status": row[2],
            "manifest_hash": row[3],
            "created_at": str(row[4]),
            "updated_at": str(row[5]),
        }

    def list_services(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT service_id, manifest, status, manifest_hash FROM services "
            "WHERE (? IS NULL OR services.status = ?) ORDER BY created_at",
            [status, status],
        ).fetchall()
        return [
            {
                "service_id": r[0],
                "manifest": json.loads(r[1]),
                "status": r[2],
                "manifest_hash": r[3],
            }
            for r in rows
        ]

    def catalog_etag(self) -> str:
        row = self.conn.execute(
            "SELECT count(*), coalesce(max(updated_at), TIMESTAMP '1970-01-01') FROM services"
        ).fetchone()
        if row is None:  # 聚合查询恒有返回，防御分支
            return 'W/"0-1970-01-01"'
        return f'W/"{row[0]}-{row[1]}"'

    # ---- api keys ----

    def insert_api_key(
        self, key_id: str, key_hash: str, consumer_wallet: str, quota_raw: int | None
    ) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO api_keys (key_id, key_hash, consumer_wallet, quota_raw) "
                "VALUES (?, ?, ?, ?)",
                [key_id, key_hash, consumer_wallet, quota_raw],
            )

    def find_api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT key_id, consumer_wallet, quota_raw, status FROM api_keys WHERE key_hash = ?",
            [key_hash],
        ).fetchone()
        if row is None:
            return None
        return {
            "key_id": row[0],
            "consumer_wallet": row[1],
            "quota_raw": row[2],
            "status": row[3],
        }

    def api_key_created_at(self, key_id: str) -> str:
        row = self.conn.execute(
            "SELECT created_at FROM api_keys WHERE key_id = ?", [key_id]
        ).fetchone()
        return str(row[0]) if row is not None else ""

    def set_api_key_status(self, key_id: str, status: str) -> None:
        with self._lock:
            self.conn.execute("UPDATE api_keys SET status = ? WHERE key_id = ?", [status, key_id])

    def close(self) -> None:
        with self._lock:
            self.conn.close()
