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

SERVICE_CREDENTIALS_DDL = """
CREATE TABLE IF NOT EXISTS service_credentials (
  service_id     VARCHAR PRIMARY KEY,
  headers_cipher BLOB,
  header_names   JSON,
  updated_at     TIMESTAMP DEFAULT now()
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

PROVIDERS_DDL = """
CREATE TABLE IF NOT EXISTS providers (
  agent_id     BIGINT PRIMARY KEY,
  display_name VARCHAR,
  wallet       VARCHAR,
  claim_wallet VARCHAR,
  created_at   TIMESTAMP DEFAULT now()
)
"""

CHARGED_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS charged_events (
  tx_hash      VARCHAR,
  log_index    INTEGER,
  block_number BIGINT,
  provider     VARCHAR,
  payer        VARCHAR,
  value_raw    BIGINT,
  nonce        VARCHAR,
  PRIMARY KEY (tx_hash, log_index)
)
"""

WATERMARKS_DDL = """
CREATE TABLE IF NOT EXISTS sync_watermarks (
  stream       VARCHAR PRIMARY KEY,
  block_number BIGINT,
  updated_at   TIMESTAMP DEFAULT now()
)
"""

FEEDBACK_NULLIFIERS_DDL = """
CREATE TABLE IF NOT EXISTS feedback_nullifiers (
  receipt_id VARCHAR PRIMARY KEY,
  service_id VARCHAR,
  created_at TIMESTAMP DEFAULT now()
)
"""

FEEDBACK_DDL = """
CREATE TABLE IF NOT EXISTS feedback (
  receipt_id VARCHAR PRIMARY KEY,
  service_id VARCHAR,
  rating     INTEGER,
  comment    VARCHAR,
  created_at TIMESTAMP DEFAULT now()
)
"""

ANCHOR_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS anchor_records (
  provider_agent_id BIGINT,
  digest            VARCHAR,
  tx_hash           VARCHAR,
  anchored_at       TIMESTAMP DEFAULT now(),
  PRIMARY KEY (provider_agent_id, digest)
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
            self.conn.execute(PROVIDERS_DDL)
            # 旧库迁移：providers 补 claim_wallet 列（2026-10-06 认领语义）
            cols = self.conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='providers'"
            ).fetchall()
            if "claim_wallet" not in [c[0] for c in cols]:
                self.conn.execute("ALTER TABLE providers ADD COLUMN claim_wallet VARCHAR")
            self.conn.execute(CHARGED_EVENTS_DDL)
            self.conn.execute(WATERMARKS_DDL)
            self.conn.execute(SERVICE_CREDENTIALS_DDL)
            # 决策层（10 篇）：反馈核销/反馈原始/聚合摘要锚定记录
            self.conn.execute(FEEDBACK_NULLIFIERS_DDL)
            self.conn.execute(FEEDBACK_DDL)
            self.conn.execute(ANCHOR_RECORDS_DDL)

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

    def upsert_service_credentials(self, service_id: str, cipher: bytes, names: list[str]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO service_credentials "
                "(service_id, headers_cipher, header_names) VALUES (?, ?, ?)",
                [service_id, cipher, json.dumps(names)],
            )

    def get_service_credentials_cipher(self, service_id: str) -> tuple[bytes, list[str]] | None:
        row = self.conn.execute(
            "SELECT headers_cipher, header_names FROM service_credentials WHERE service_id = ?",
            [service_id],
        ).fetchone()
        if row is None:
            return None
        return bytes(row[0]), list(json.loads(row[1]))

    def delete_service_credentials(self, service_id: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM service_credentials WHERE service_id = ?", [service_id])

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

    def get_api_key(self, key_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT key_id, consumer_wallet, quota_raw, status, created_at "
            "FROM api_keys WHERE key_id = ?",
            [key_id],
        ).fetchone()
        if row is None:
            return None
        return {
            "key_id": row[0],
            "consumer_wallet": row[1],
            "quota_raw": row[2],
            "status": row[3],
            "created_at": str(row[4]),
        }

    def list_api_keys(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT key_id, consumer_wallet, quota_raw, status, created_at "
            "FROM api_keys ORDER BY created_at, key_id"
        ).fetchall()
        return [
            {
                "key_id": r[0],
                "consumer_wallet": r[1],
                "quota_raw": r[2],
                "status": r[3],
                "created_at": str(r[4]),
            }
            for r in rows
        ]

    def update_api_key_wallet(self, key_id: str, consumer_wallet: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE api_keys SET consumer_wallet = ? WHERE key_id = ?",
                [consumer_wallet, key_id],
            )

    # ---- providers（01 §4）----

    def upsert_provider(
        self, agent_id: int, display_name: str, wallet: str, claim_wallet: str | None = None
    ) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO providers (agent_id, display_name, wallet, claim_wallet) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (agent_id) DO UPDATE SET "
                "display_name = excluded.display_name, wallet = excluded.wallet, "
                "claim_wallet = COALESCE(providers.claim_wallet, excluded.claim_wallet)",
                [agent_id, display_name, wallet, claim_wallet],
            )

    def get_provider(self, agent_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT agent_id, display_name, wallet, claim_wallet, created_at "
            "FROM providers WHERE agent_id = ?",
            [agent_id],
        ).fetchone()
        if row is None:
            return None
        return {
            "agent_id": row[0],
            "display_name": row[1],
            "wallet": row[2],
            "claim_wallet": row[3],
            "created_at": str(row[4]),
        }

    def list_providers(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT agent_id, display_name, wallet, claim_wallet, created_at FROM providers "
            "ORDER BY created_at, agent_id"
        ).fetchall()
        return [
            {
                "agent_id": r[0],
                "display_name": r[1],
                "wallet": r[2],
                "claim_wallet": r[3],
                "created_at": str(r[4]),
            }
            for r in rows
        ]

    def find_services_by_agent(self, agent_id: int) -> list[dict[str, Any]]:
        """providers ↔ manifests 关联：manifest->provider.agent_id 提取匹配（01 §5）。"""
        rows = self.conn.execute(
            "SELECT service_id, manifest, status, manifest_hash FROM services "
            "WHERE CAST(json_extract_string(manifest, '$.provider.agent_id') AS BIGINT) = ? "
            "ORDER BY created_at",
            [agent_id],
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

    # ---- charged_events（06：链上 Charged 收入真相库存）----

    def _charged_count(self) -> int:
        row = self.conn.execute("SELECT count(*) FROM charged_events").fetchone()
        return int(row[0]) if row is not None else 0  # 聚合恒有返回，防御分支

    def insert_charged_events(self, events: list[dict[str, Any]]) -> int:
        """幂等入库（tx_hash+log_index 主键冲突跳过）；返回新插入行数。"""
        with self._lock:
            before = self._charged_count()
            for ev in events:
                self.conn.execute(
                    "INSERT INTO charged_events "
                    "(tx_hash, log_index, block_number, provider, payer, value_raw, nonce) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    [
                        ev["tx_hash"],
                        ev["log_index"],
                        ev["block_number"],
                        ev["provider"],
                        ev["payer"],
                        ev["value_raw"],
                        ev["nonce"],
                    ],
                )
            after = self._charged_count()
        return after - before

    def charged_by_provider(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT provider, sum(value_raw) AS revenue_raw, count(*) AS charged_count, "
            "max(block_number) AS last_block FROM charged_events GROUP BY provider"
        ).fetchall()
        return [
            {
                "provider": r[0],
                "revenue_raw": int(r[1] or 0),
                "charged_count": int(r[2]),
                "last_block": int(r[3] or 0),
            }
            for r in rows
        ]

    def charged_total(self) -> dict[str, int]:
        row = self.conn.execute(
            "SELECT coalesce(sum(value_raw), 0), count(*), "
            "coalesce(max(block_number), 0) FROM charged_events"
        ).fetchone()
        if row is None:  # 聚合查询恒有返回，防御分支
            return {"gmv_raw": 0, "charged_count": 0, "last_block": 0}
        return {"gmv_raw": int(row[0]), "charged_count": int(row[1]), "last_block": int(row[2])}

    def charged_events_for_provider(self, provider: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT tx_hash, log_index, block_number, value_raw, nonce FROM charged_events "
            "WHERE provider = ? ORDER BY block_number, log_index",
            [provider],
        ).fetchall()
        return [
            {
                "tx_hash": r[0],
                "log_index": int(r[1]),
                "block_number": int(r[2]),
                "value_raw": int(r[3]),
                "nonce": r[4],
            }
            for r in rows
        ]

    # ---- 水位（charged_events 增量拉取游标）----

    def get_watermark(self, stream: str) -> int | None:
        row = self.conn.execute(
            "SELECT block_number FROM sync_watermarks WHERE stream = ?", [stream]
        ).fetchone()
        return int(row[0]) if row is not None else None

    def set_watermark(self, stream: str, block_number: int) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO sync_watermarks (stream, block_number, updated_at) "
                "VALUES (?, ?, now()) ON CONFLICT (stream) DO UPDATE SET "
                "block_number = excluded.block_number, updated_at = now()",
                [stream, block_number],
            )

    # ---- feedback（10 §2：收据绑定 + 一次性核销）----

    def claim_feedback(
        self, receipt_id: str, service_id: str, rating: int, comment: str | None
    ) -> bool:
        """核销收据并落反馈（单锁内原子完成）；receipt_id 已核销 → False（409 语义）。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM feedback_nullifiers WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
            if row is not None:
                return False
            self.conn.execute(
                "INSERT INTO feedback_nullifiers (receipt_id, service_id) VALUES (?, ?)",
                [receipt_id, service_id],
            )
            self.conn.execute(
                "INSERT INTO feedback (receipt_id, service_id, rating, comment) "
                "VALUES (?, ?, ?, ?)",
                [receipt_id, service_id, rating, comment],
            )
            return True

    def list_feedback(self, service_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT receipt_id, rating, comment, created_at FROM feedback "
            "WHERE service_id = ? ORDER BY created_at, receipt_id",
            [service_id],
        ).fetchall()
        return [
            {
                "receipt_id": r[0],
                "rating": int(r[1]),
                "comment": r[2],
                "created_at": str(r[3]),
            }
            for r in rows
        ]

    def feedback_by_service(self) -> dict[str, dict[str, Any]]:
        """全库按服务聚合（决策反馈分量的批量取数口径）。"""
        rows = self.conn.execute(
            "SELECT service_id, count(*), avg(rating) FROM feedback GROUP BY service_id"
        ).fetchall()
        return {
            r[0]: {"count": int(r[1]), "avg": float(r[2]) if r[2] is not None else None}
            for r in rows
        }

    def feedback_global(self) -> dict[str, Any]:
        """全局均值=贝叶斯先验（10 §3）。"""
        row = self.conn.execute("SELECT count(*), avg(rating) FROM feedback").fetchone()
        if row is None:  # 聚合查询恒有返回，防御分支
            return {"count": 0, "avg": None}
        return {"count": int(row[0]), "avg": float(row[1]) if row[1] is not None else None}

    def feedback_count_since(self, service_id: str, since: str) -> int:
        """窗口内该服务已受理反馈数（窗口限频口径，CONSTRAINTS §E）。"""
        row = self.conn.execute(
            "SELECT count(*) FROM feedback WHERE service_id = ? AND created_at >= ?::TIMESTAMP",
            [service_id, since],
        ).fetchone()
        return int(row[0]) if row is not None else 0

    # ---- anchor_records（10 §1：聚合摘要锚链记录）----

    def anchor_record(self, provider_agent_id: int, digest: str, tx_hash: str) -> bool:
        """幂等落锚定记录；(agent, digest) 已存在 → False（duplicate 语义）。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM anchor_records WHERE provider_agent_id = ? AND digest = ?",
                [provider_agent_id, digest],
            ).fetchone()
            if row is not None:
                return False
            self.conn.execute(
                "INSERT INTO anchor_records (provider_agent_id, digest, tx_hash) VALUES (?, ?, ?)",
                [provider_agent_id, digest, tx_hash],
            )
            return True

    def anchor_find(self, provider_agent_id: int, digest: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT provider_agent_id, digest, tx_hash, anchored_at FROM anchor_records "
            "WHERE provider_agent_id = ? AND digest = ?",
            [provider_agent_id, digest],
        ).fetchone()
        if row is None:
            return None
        return {
            "provider_agent_id": int(row[0]),
            "digest": row[1],
            "tx_hash": row[2],
            "anchored_at": str(row[3]),
        }

    def anchor_latest_map(self) -> dict[int, dict[str, Any]]:
        """agent_id → 最新一条锚定记录（决策行 anchor_tx 透出口径）。"""
        rows = self.conn.execute(
            "SELECT provider_agent_id, digest, tx_hash, anchored_at FROM anchor_records "
            "ORDER BY anchored_at"
        ).fetchall()
        out: dict[int, dict[str, Any]] = {}
        for r in rows:
            out[int(r[0])] = {
                "provider_agent_id": int(r[0]),
                "digest": r[1],
                "tx_hash": r[2],
                "anchored_at": str(r[3]),
            }
        return out

    def close(self) -> None:
        with self._lock:
            self.conn.close()
