"""DuckDB 分析事实库（04 篇 DDL + network 列；单连接 + 线程锁单写；INSERT OR REPLACE 幂等）。"""

import threading
from pathlib import Path
from typing import Any

import duckdb

DDL = [
    """
    CREATE TABLE IF NOT EXISTS blocks (
        network      VARCHAR,
        number       BIGINT,
        hash         VARCHAR,
        timestamp    TIMESTAMP,
        tx_count     INTEGER,
        gas_used     BIGINT,
        miner        VARCHAR,
        synced_at    TIMESTAMP DEFAULT now(),
        PRIMARY KEY (network, number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions (
        network      VARCHAR,
        hash         VARCHAR,
        block_number BIGINT,
        tx_index     INTEGER,
        from_addr    VARCHAR,
        to_addr      VARCHAR,
        value_wei    DECIMAL(38,0),
        gas_used     BIGINT,
        status       SMALLINT,
        tx_type      SMALLINT,
        synced_at    TIMESTAMP DEFAULT now(),
        PRIMARY KEY (network, hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS logs (
        network      VARCHAR,
        log_index    INTEGER,
        block_number BIGINT,
        tx_hash      VARCHAR,
        address      VARCHAR,
        topic0       VARCHAR,
        topics       VARCHAR[],
        data         VARCHAR,
        PRIMARY KEY (network, tx_hash, log_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS token_transfers (
        network      VARCHAR,
        tx_hash      VARCHAR,
        log_index    INTEGER,
        block_number BIGINT,
        token        VARCHAR,
        token_kind   SMALLINT,
        from_addr    VARCHAR,
        to_addr      VARCHAR,
        amount       DECIMAL(38,0),
        PRIMARY KEY (network, tx_hash, log_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_identities (
        network      VARCHAR,
        token_id     BIGINT,
        owner        VARCHAR,
        agent_wallet VARCHAR,
        token_uri    VARCHAR,
        minted_block BIGINT,
        synced_at    TIMESTAMP DEFAULT now(),
        PRIMARY KEY (network, token_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_state (
        network      VARCHAR,
        stream       VARCHAR,
        last_block   BIGINT,
        updated_at   TIMESTAMP DEFAULT now(),
        PRIMARY KEY (network, stream)
    )
    """,
]


class DuckStore:
    """分析库封装：全部写经线程锁串行（DuckDB 单写者约束）。"""

    def __init__(self, path: Path | str, network: str = "testnet") -> None:
        self._network = network
        if isinstance(path, str):
            path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(path))
        self._lock = threading.Lock()
        with self._lock:
            for stmt in DDL:
                self._conn.execute(stmt)

    # ---- 通用 -------------------------------------------------------------
    def query(self, sql: str, params: list[Any] | None = None) -> list[tuple]:
        with self._lock:
            cur = self._conn.execute(sql, params or [])
            return cur.fetchall()

    def _exec(self, sql: str, params: list[Any]) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    # ---- 表写入（幂等 upsert）---------------------------------------------
    def upsert_block(
        self, *, number: int, hash: str, timestamp: int, tx_count: int, gas_used: int, miner: str
    ) -> None:
        self._exec(
            "INSERT OR REPLACE INTO blocks"
            " (network, number, hash, timestamp, tx_count, gas_used, miner)"
            " VALUES (?, ?, ?, to_timestamp(?), ?, ?, ?)",
            [self._network, number, hash, timestamp, tx_count, gas_used, miner],
        )

    def upsert_transaction(
        self,
        *,
        hash: str,
        block_number: int,
        tx_index: int,
        from_addr: str,
        to_addr: str | None,
        value_wei: int,
        gas_used: int,
        status: int,
        tx_type: int,
    ) -> None:
        self._exec(
            "INSERT OR REPLACE INTO transactions"
            " (network, hash, block_number, tx_index, from_addr, to_addr,"
            "  value_wei, gas_used, status, tx_type)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                self._network,
                hash,
                block_number,
                tx_index,
                from_addr,
                to_addr,
                value_wei,
                gas_used,
                status,
                tx_type,
            ],
        )

    def upsert_log(
        self,
        *,
        log_index: int,
        block_number: int,
        tx_hash: str,
        address: str,
        topic0: str,
        topics: list[str],
        data: str,
    ) -> None:
        self._exec(
            "INSERT OR REPLACE INTO logs"
            " (network, log_index, block_number, tx_hash, address, topic0, topics, data)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [self._network, log_index, block_number, tx_hash, address, topic0, topics, data],
        )

    def upsert_token_transfer(
        self,
        *,
        tx_hash: str,
        log_index: int,
        block_number: int,
        token: str,
        token_kind: int,
        from_addr: str,
        to_addr: str,
        amount: int,
    ) -> None:
        self._exec(
            "INSERT OR REPLACE INTO token_transfers"
            " (network, tx_hash, log_index, block_number, token, token_kind,"
            "  from_addr, to_addr, amount)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                self._network,
                tx_hash,
                log_index,
                block_number,
                token,
                token_kind,
                from_addr,
                to_addr,
                amount,
            ],
        )

    def upsert_agent_identity(
        self, *, token_id: int, owner: str, agent_wallet: str, token_uri: str, minted_block: int
    ) -> None:
        self._exec(
            "INSERT OR REPLACE INTO agent_identities"
            " (network, token_id, owner, agent_wallet, token_uri, minted_block)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [self._network, token_id, owner, agent_wallet, token_uri, minted_block],
        )

    # ---- 水位 -------------------------------------------------------------
    def get_watermark(self, stream: str) -> int | None:
        rows = self.query(
            "SELECT last_block FROM sync_state WHERE network = ? AND stream = ?",
            [self._network, stream],
        )
        return int(rows[0][0]) if rows else None

    def set_watermark(self, stream: str, last_block: int) -> None:
        self._exec(
            "INSERT OR REPLACE INTO sync_state (network, stream, last_block, updated_at)"
            " VALUES (?, ?, ?, now())",
            [self._network, stream, last_block],
        )

    # ---- 查询视图 ----------------------------------------------------------
    def recent_token_transfers(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT tx_hash, block_number, token, token_kind, from_addr, to_addr, amount"
            " FROM token_transfers WHERE network = ? ORDER BY block_number DESC, log_index DESC"
            " LIMIT ?",
            [self._network, limit],
        )
        keys = ("tx_hash", "block_number", "token", "token_kind", "from_addr", "to_addr", "amount")
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def status(self) -> dict[str, int | None]:
        counts: dict[str, int | None] = {}
        for table in ("blocks", "transactions", "logs", "token_transfers", "agent_identities"):
            rows = self.query(f"SELECT count(*) FROM {table} WHERE network = ?", [self._network])  # noqa: S608
            counts[table] = int(rows[0][0])
        rows = self.query(
            "SELECT stream, last_block FROM sync_state WHERE network = ?", [self._network]
        )
        for stream, last in rows:
            counts[f"watermark:{stream}"] = int(last)
        return counts

    def close(self) -> None:
        with self._lock:
            self._conn.close()
