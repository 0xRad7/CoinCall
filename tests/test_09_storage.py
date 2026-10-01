"""unit：DuckDB/Redis/幂等（02 篇 test_09 矩阵，全离线：tmp 库 + fakeredis）。"""

import pytest
from app.core.idempotency import IdempotencyStore
from app.storage.duckdb import DuckStore
from app.storage.redis_store import RedisStore
from fakeredis import FakeRedis

pytestmark = pytest.mark.unit


@pytest.fixture
def store(tmp_path) -> DuckStore:
    s = DuckStore(tmp_path / "test.duckdb", network="testnet")
    yield s
    s.close()


class TestDuckStore:
    def test_init_creates_tables(self, store: DuckStore) -> None:
        tables = {r[0] for r in store.query("SHOW TABLES")}
        assert {
            "blocks",
            "transactions",
            "logs",
            "token_transfers",
            "agent_identities",
            "sync_state",
        } <= tables

    def test_upsert_block_idempotent(self, store: DuckStore) -> None:
        store.upsert_block(
            number=1, hash="0xa", timestamp=100, tx_count=2, gas_used=100, miner="0xm"
        )
        store.upsert_block(
            number=1, hash="0xa", timestamp=100, tx_count=2, gas_used=100, miner="0xm"
        )
        rows = store.query("SELECT count(*) c FROM blocks")
        assert rows[0][0] == 1

    def test_upsert_transaction_and_replacement(self, store: DuckStore) -> None:
        store.upsert_transaction(
            hash="0xt1",
            block_number=1,
            tx_index=0,
            from_addr="0xf",
            to_addr="0xt",
            value_wei=10,
            gas_used=21000,
            status=1,
            tx_type=0,
        )
        store.upsert_transaction(  # 同主键覆盖
            hash="0xt1",
            block_number=1,
            tx_index=0,
            from_addr="0xf",
            to_addr="0xt",
            value_wei=10,
            gas_used=22000,
            status=1,
            tx_type=0,
        )
        rows = store.query("SELECT gas_used FROM transactions WHERE hash='0xt1'")
        assert rows[0][0] == 22000

    def test_upsert_log_and_transfer_normalization(self, store: DuckStore) -> None:
        store.upsert_log(
            log_index=0,
            block_number=1,
            tx_hash="0xt1",
            address="0xtoken",
            topic0="0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
            topics=["0x1", "0x2", "0x3"],
            data="0x",
        )
        store.upsert_token_transfer(
            tx_hash="0xt1",
            log_index=0,
            block_number=1,
            token="0xtoken",
            token_kind=20,
            from_addr="0x1",
            to_addr="0x2",
            amount=100,
        )
        assert store.query("SELECT count(*) c FROM logs")[0][0] == 1
        assert store.query("SELECT token_kind FROM token_transfers")[0][0] == 20

    def test_agent_identity_upsert(self, store: DuckStore) -> None:
        store.upsert_agent_identity(
            token_id=7,
            owner="0xo",
            agent_wallet="0x0",
            token_uri="ipfs://x",
            minted_block=100,
        )
        store.upsert_agent_identity(  # 对账覆盖
            token_id=7,
            owner="0xo2",
            agent_wallet="0x0",
            token_uri="ipfs://x",
            minted_block=100,
        )
        assert store.query("SELECT owner FROM agent_identities WHERE token_id=7")[0][0] == "0xo2"

    def test_sync_state_watermark(self, store: DuckStore) -> None:
        assert store.get_watermark("logs") is None
        store.set_watermark("logs", 123)
        store.set_watermark("logs", 456)
        assert store.get_watermark("logs") == 456

    def test_recent_transfers_query(self, store: DuckStore) -> None:
        for i in range(5):
            store.upsert_token_transfer(
                tx_hash=f"0xt{i}",
                log_index=0,
                block_number=i,
                token="0xtoken",
                token_kind=20,
                from_addr="0xa",
                to_addr="0xb",
                amount=i,
            )
        rows = store.recent_token_transfers(limit=3)
        assert len(rows) == 3
        assert rows[0]["block_number"] == 4  # 最新在前


class TestRedisStore:
    @pytest.fixture
    def redis_store(self) -> RedisStore:
        return RedisStore(FakeRedis())

    def test_nonce_lock_mutual_exclusion(self, redis_store: RedisStore) -> None:
        assert redis_store.acquire_nonce_lock("0xa") is True
        assert redis_store.acquire_nonce_lock("0xa") is False  # 互斥
        redis_store.release_nonce_lock("0xa")
        assert redis_store.acquire_nonce_lock("0xa") is True

    def test_cache_json_roundtrip_and_ttl(self, redis_store: RedisStore) -> None:
        redis_store.cache_set("k", {"v": 1}, ttl=60)
        assert redis_store.cache_get("k") == {"v": 1}

    def test_idem_set_once(self, redis_store: RedisStore) -> None:
        assert redis_store.idem_put("key1", {"r": 1}, ttl=60) is True
        assert redis_store.idem_put("key1", {"r": 2}, ttl=60) is False
        assert redis_store.idem_get("key1") == {"r": 1}

    def test_publish_no_error_without_subscribers(self, redis_store: RedisStore) -> None:
        redis_store.publish("ch:blocks:testnet", {"number": 1})


class TestIdempotencyStore:
    def test_memory_fallback(self) -> None:
        store = IdempotencyStore(redis=None)
        assert store.check_and_reserve("k", {"a": 1}) is True
        assert store.check_and_reserve("k", {"a": 1}) is False
        assert store.check_and_reserve("k", {"a": 2}) is False  # 同 key 不同 body 也拒
        assert store.check_and_reserve("k2", {"a": 2}) is True
