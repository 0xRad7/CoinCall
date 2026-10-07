"""T15（09 P0-5）：keeper 结算器四路单测 + 事件解码 + bot-chain-api 客户端。

四路：正常批 / 含坏账批（transfer_failed 重试一次→bad_debt+拉黑）/ 过期批
（validBefore 已过 + 链上 not_in_window/nonce_used）/ kill-重启续批
（进程重启续批 + 提交后宕机经 usedNonces 探测恢复，不丢单）。

零网络：SettleChain 全 fake；BotChainSettleChain 经 respx（仅方法级装饰器）+
_fetch_receipt 注入；DuckDB 一律 tmp_path（A2）。
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from eth_abi import encode as abi_encode
from eth_utils import keccak

from app.modules.calls import CallRecord, CallStore
from app.modules.keeper import (
    BotChainSettleChain,
    Keeper,
    SettleChainError,
    decode_charge_events,
)
from tests.conftest import API_KEY, CONSUMER_WALLET, make_x_payment_header

pytestmark = pytest.mark.unit

VAULT = "0x000000000000000000000000000000000000dEaD"
CONSUMER_A = CONSUMER_WALLET
CONSUMER_B = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
PROVIDER_WALLET = "0x1234567890AbCdEf1234567890aBcDeF12345678"
OTHER_TOKEN = "0x9999999999999999999999999999999999999999"
VALID_BEFORE_FAR = 4_102_444_800  # 2100-01-01
NOW = 1_700_000_000.0


def _pad_addr(addr: str) -> str:
    return "0x" + addr[2:].lower().rjust(64, "0")


def _log_base(address: str) -> dict[str, Any]:
    return {
        "address": address,
        "blockNumber": 1,
        "blockHash": "0x" + "00" * 32,
        "transactionHash": "0x" + "00" * 32,
        "transactionIndex": 0,
        "logIndex": 0,
        "removed": False,
    }


def log_charged(
    *, provider: str, from_: str, value: int, nonce: str, address: str = VAULT
) -> dict[str, Any]:
    log = _log_base(address)
    log["topics"] = [
        "0x" + keccak(text="Charged(address,address,uint256,bytes32)").hex(),
        _pad_addr(provider),
        _pad_addr(from_),
        nonce,
    ]
    log["data"] = "0x" + abi_encode(["uint256"], [value]).hex()
    return log


def log_failed(*, provider: str, from_: str, reason: str, address: str = VAULT) -> dict[str, Any]:
    log = _log_base(address)
    log["topics"] = [
        "0x" + keccak(text="ChargeFailed(address,address,string)").hex(),
        _pad_addr(provider),
        _pad_addr(from_),
    ]
    log["data"] = "0x" + abi_encode(["string"], [reason]).hex()
    return log


def log_transfer(*, token: str, from_: str, to: str, value: int) -> dict[str, Any]:
    """非 PayVault 日志（MockUSDT Transfer）：keeper 必须按合约地址过滤。"""
    log = _log_base(token)
    log["topics"] = [
        "0x" + keccak(text="Transfer(address,address,uint256)").hex(),
        _pad_addr(from_),
        _pad_addr(to),
    ]
    log["data"] = "0x" + abi_encode(["uint256"], [value]).hex()
    return log


def nonce_of(i: int) -> str:
    return "0x" + f"{i:064x}"


def seed_settle(
    store: CallStore,
    *,
    call_id: str,
    agent_id: int = 137,
    wallet: str = CONSUMER_A,
    value: str = "10000",
    nonce: str,
    valid_before: int = VALID_BEFORE_FAR,
) -> None:
    from app.modules.calls import CallStatus

    store.insert_call(
        CallRecord(
            call_id=call_id,
            service_id="svc_translate_v1",
            provider_agent_id=agent_id,
            consumer_key_id=f"key_{call_id}",
            consumer_wallet=wallet,
            amount_raw=int(value),
            payment_nonce=nonce,
        )
    )
    store.mark_call(call_id, CallStatus.SUCCESS)  # 真实时序：settle 入队前已是 success
    store.insert_settle(
        call_id=call_id,
        provider_token_id=agent_id,
        auth={
            "from": wallet,
            "to": VAULT,
            "value": value,
            "validAfter": 0,
            "validBefore": valid_before,
            "nonce": nonce,
            "v": 27,
            "r": "0x" + "11" * 32,
            "s": "0x" + "22" * 32,
        },
    )


class FakeChain:
    """SettleChain fake：按提交序回放预置日志；记录 submit 载荷。"""

    def __init__(
        self,
        events_by_tx: list[list[dict[str, Any]]] | None = None,
        used_nonces: set[str] | None = None,
        submit_error: Exception | None = None,
    ) -> None:
        self.submits: list[list[dict[str, Any]]] = []
        self.events_by_tx = list(events_by_tx or [])
        self.used_nonces = {n.lower() for n in (used_nonces or set())}
        self.submit_error = submit_error
        self._seq = 0

    async def submit_batch(self, calls: list[dict[str, Any]]) -> str:
        if self.submit_error is not None:
            raise self.submit_error
        self.submits.append(calls)
        self._seq += 1
        return f"0x{self._seq:064x}"

    async def receipt_status(self, tx_hash: str) -> int | None:
        return 1

    async def receipt_logs(self, tx_hash: str) -> list[dict[str, Any]]:
        idx = int(tx_hash, 16) - 1
        return list(self.events_by_tx[idx]) if 0 <= idx < len(self.events_by_tx) else []

    async def is_nonce_used(self, nonce: str) -> bool:
        return nonce.lower() in self.used_nonces


async def wallet_for(_row: dict[str, Any]) -> str | None:
    return PROVIDER_WALLET


def make_keeper(
    store: CallStore,
    chain: FakeChain,
    *,
    batch_size: int = 3,
    flush_interval: float = 30.0,
) -> tuple[Keeper, dict[str, float]]:
    clock = {"t": NOW}
    keeper = Keeper(
        store=store,
        chain=chain,
        wallet_for=wallet_for,
        batch_size=batch_size,
        flush_interval=flush_interval,
        now=lambda: clock["t"],
    )
    return keeper, clock


def settle_statuses(store: CallStore) -> dict[str, list[str]]:
    rows = store.conn.execute(
        "SELECT call_id, status FROM settle_queue ORDER BY call_id"
    ).fetchall()
    out: dict[str, list[str]] = {}
    for call_id, status in rows:
        out.setdefault(status, []).append(call_id)
    return out


def call_statuses(store: CallStore) -> dict[str, str]:
    rows = store.conn.execute("SELECT call_id, status FROM calls ORDER BY call_id").fetchall()
    return dict(rows)


# ---- 事件解码（离线，含外来日志过滤） --------------------------------------


async def test_decode_events_filters_foreign_logs_and_decodes() -> None:
    logs = [
        log_transfer(token=OTHER_TOKEN, from_=CONSUMER_A, to=VAULT, value=10000),
        log_charged(provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(1)),
        log_failed(provider=PROVIDER_WALLET, from_=CONSUMER_B, reason="transfer_failed"),
    ]
    events = decode_charge_events(logs, VAULT)
    assert [e.kind for e in events] == ["charged", "failed"]
    charged = events[0]
    assert charged.provider.lower() == PROVIDER_WALLET.lower()
    assert charged.from_.lower() == CONSUMER_A.lower()
    assert charged.value == 10000
    assert charged.nonce == nonce_of(1)
    failed = events[1]
    assert failed.reason == "transfer_failed"
    assert failed.value is None and failed.nonce is None


# ---- 路径一：正常批 --------------------------------------------------------


async def test_normal_batch_settles_all(tmp_path: Path) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(store, call_id=f"call_{i}", nonce=nonce_of(i))
    chain = FakeChain(
        events_by_tx=[
            [
                log_charged(
                    provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(i)
                )
                for i in range(1, 4)
            ]
        ]
    )
    keeper, _clock = make_keeper(store, chain)

    report = await keeper.run_cycle()

    assert report["charged"] == 3 and report["charged_raw"] == 30000
    assert len(chain.submits) == 1 and len(chain.submits[0]) == 3
    sent = chain.submits[0][0]
    assert sent["provider"].lower() == PROVIDER_WALLET.lower()
    assert sent["auth"]["from"].lower() == CONSUMER_A.lower()
    assert sent["auth"]["value"] == 10000
    assert sent["auth"]["nonce"] == nonce_of(1)
    assert settle_statuses(store) == {"done": ["call_1", "call_2", "call_3"]}
    assert set(call_statuses(store).values()) == {"settled"}
    assert keeper.metrics.cumulative_charged_raw == 30000
    assert keeper.metrics.cumulative_charged_count == 3
    store.close()


async def test_below_threshold_waits_then_flushes_on_interval(tmp_path: Path) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    seed_settle(store, call_id="call_1", nonce=nonce_of(1))
    seed_settle(store, call_id="call_2", nonce=nonce_of(2))
    chain = FakeChain(events_by_tx=[[]])
    keeper, clock = make_keeper(store, chain)

    first = await keeper.run_cycle()
    assert first["flushed"] is False and chain.submits == []

    clock["t"] += 31.0  # 越过 flush_interval
    second = await keeper.run_cycle()
    assert second["flushed"] is True and len(chain.submits) == 1
    store.close()


async def test_wallet_resolution_failure_keeps_pending(tmp_path: Path) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(store, call_id=f"call_{i}", nonce=nonce_of(i))
    chain = FakeChain(events_by_tx=[[]])

    async def no_wallet(_row: dict[str, Any]) -> str | None:
        return None

    keeper = Keeper(store=store, chain=chain, wallet_for=no_wallet, batch_size=3, now=lambda: NOW)
    report = await keeper.run_cycle()
    assert report["flushed"] is False and report["unresolved"] == 3
    assert chain.submits == []
    assert len(settle_statuses(store)["pending"]) == 3
    store.close()


async def test_submit_error_keeps_pending_and_records(tmp_path: Path) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(store, call_id=f"call_{i}", nonce=nonce_of(i))
    chain = FakeChain(submit_error=SettleChainError("bot-chain-api 502"))
    keeper, _clock = make_keeper(store, chain)

    with pytest.raises(SettleChainError):
        await keeper.run_cycle()

    assert keeper.metrics.last_error is not None
    assert len(settle_statuses(store)["pending"]) == 3  # 不丢单：nonce 未烧，可重试
    store.close()


# ---- 路径二：含坏账批 ------------------------------------------------------


async def test_transfer_failed_retries_once_then_bad_debt(tmp_path: Path) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(
            store,
            call_id=f"call_{i}",
            wallet=CONSUMER_A if i < 3 else CONSUMER_B,
            nonce=nonce_of(i),
        )
    # 批内：A 两笔 Charged，B 一笔 transfer_failed（nonce 未烧，可原签名重试）
    chain = FakeChain(
        events_by_tx=[
            [
                log_charged(
                    provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(1)
                ),
                log_charged(
                    provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(2)
                ),
                log_failed(provider=PROVIDER_WALLET, from_=CONSUMER_B, reason="transfer_failed"),
            ],
            [log_failed(provider=PROVIDER_WALLET, from_=CONSUMER_B, reason="transfer_failed")],
        ]
    )
    keeper, clock = make_keeper(store, chain)

    first = await keeper.run_cycle()
    assert first["charged"] == 2 and first["retried"] == 1
    statuses = settle_statuses(store)
    assert statuses["done"] == ["call_1", "call_2"] and statuses["pending"] == ["call_3"]
    assert call_statuses(store)["call_3"] == "success"  # 尚未坏账
    assert not keeper.blacklisted(CONSUMER_B)

    clock["t"] += 31.0  # 时间阈值触发重试批
    second = await keeper.run_cycle()
    assert second["failed"] == 1

    statuses = settle_statuses(store)
    assert statuses.get("failed") == ["call_3"]
    assert call_statuses(store)["call_3"] == "bad_debt"
    assert keeper.blacklisted(CONSUMER_B)  # 拉黑联动
    assert keeper.metrics.cumulative_charged_raw == 20000
    store.close()


# ---- 路径三：过期批 --------------------------------------------------------


async def test_expired_rows_are_voided_without_submission(tmp_path: Path) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    seed_settle(store, call_id="call_stale", nonce=nonce_of(1), valid_before=1_600_000_000)
    seed_settle(store, call_id="call_live", nonce=nonce_of(2))
    chain = FakeChain(events_by_tx=[[]])
    keeper, _clock = make_keeper(store, chain)  # 时钟固定 NOW=1.7e9 > 1.6e9

    report = await keeper.run_cycle()

    assert report["expired"] == 1
    assert chain.submits == []  # 过期笔不上链
    statuses = settle_statuses(store)
    assert statuses["expired"] == ["call_stale"] and statuses["pending"] == ["call_live"]
    store.close()


async def test_chain_not_in_window_and_nonce_used_mark_expired(tmp_path: Path) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(store, call_id=f"call_{i}", nonce=nonce_of(i))
    chain = FakeChain(
        events_by_tx=[
            [
                log_failed(provider=PROVIDER_WALLET, from_=CONSUMER_A, reason="not_in_window"),
                log_failed(provider=PROVIDER_WALLET, from_=CONSUMER_A, reason="nonce_used"),
                log_failed(provider=PROVIDER_WALLET, from_=CONSUMER_A, reason="nonce_used"),
            ]
        ]
    )
    keeper, _clock = make_keeper(store, chain)

    report = await keeper.run_cycle()
    assert report["expired"] == 3
    assert len(settle_statuses(store)["expired"]) == 3  # 等于从未扣款
    assert keeper.metrics.cumulative_charged_raw == 0
    store.close()


async def test_bad_signature_marks_failed_and_alarms(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(store, call_id=f"call_{i}", nonce=nonce_of(i))
    chain = FakeChain(
        events_by_tx=[
            [
                log_charged(
                    provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(1)
                ),
                log_failed(provider=PROVIDER_WALLET, from_=CONSUMER_A, reason="bad_signature"),
                log_failed(provider=PROVIDER_WALLET, from_=CONSUMER_A, reason="auth_to_mismatch"),
            ]
        ]
    )
    keeper, _clock = make_keeper(store, chain)

    with caplog.at_level(logging.ERROR, logger="coincall.gateway.keeper"):
        report = await keeper.run_cycle()

    assert report["charged"] == 1 and report["failed"] == 2
    assert "bad_signature" in caplog.text  # 网关侧验签已拦，出现即 bug 的告警
    statuses = settle_statuses(store)
    assert statuses["done"] == ["call_1"] and statuses["failed"] == ["call_2", "call_3"]
    store.close()


# ---- 路径四：kill-重启续批 -------------------------------------------------


async def test_kill_restart_resumes_pending_batch(tmp_path: Path) -> None:
    db = str(tmp_path / "gateway.duckdb")

    store1 = CallStore(db)
    for i in range(1, 4):
        seed_settle(store1, call_id=f"call_{i}", nonce=nonce_of(i))
    chain1 = FakeChain(
        events_by_tx=[
            [
                log_charged(
                    provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(i)
                )
                for i in range(1, 4)
            ]
        ]
    )
    keeper1, _clock = make_keeper(store1, chain1)
    await keeper1.run_cycle()
    store1.close()  # kill

    store2 = CallStore(db)  # 重启：新进程从 settle_queue 续批
    for i in range(4, 7):
        seed_settle(store2, call_id=f"call_{i}", nonce=nonce_of(i))
    chain2 = FakeChain(
        events_by_tx=[
            [
                log_charged(
                    provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(i)
                )
                for i in range(4, 7)
            ]
        ]
    )
    keeper2, _clock2 = make_keeper(store2, chain2)
    await keeper2.run_cycle()

    statuses = settle_statuses(store2)
    assert len(statuses["done"]) == 6  # 旧 3 笔不重结（续批不重放），新 3 笔补齐
    assert len(chain2.submits) == 1
    store2.close()


async def test_crash_after_submit_recovers_via_nonce_probe(tmp_path: Path) -> None:
    """提交后、落库前宕机：重启经 usedNonces 探测确认已 Charged，不重放不丢单。"""
    db = str(tmp_path / "gateway.duckdb")
    nonces = [nonce_of(i) for i in range(1, 4)]

    store1 = CallStore(db)
    for i, nonce in enumerate(nonces, start=1):
        seed_settle(store1, call_id=f"call_{i}", nonce=nonce)
    chain1 = FakeChain(events_by_tx=[[]])  # 宕机：回执事件未被处理
    keeper1, _clock = make_keeper(store1, chain1)
    await keeper1.run_cycle()  # 已上链提交，但事件解析"丢失"
    store1.close()

    store2 = CallStore(db)
    chain2 = FakeChain(used_nonces=set(nonces))  # 链上 nonce 已烧 = 实际已 Charged
    keeper2, _clock2 = make_keeper(store2, chain2)
    report = await keeper2.run_cycle()

    assert report["recovered"] == 3
    assert chain2.submits == []  # 不重放（重放只会得到 nonce_used 被误判 expired）
    assert len(settle_statuses(store2)["done"]) == 3
    assert keeper2.metrics.cumulative_charged_raw == 30000
    assert set(call_statuses(store2).values()) == {"settled"}
    store2.close()


# ---- 常驻任务生命周期 ------------------------------------------------------


async def test_background_task_settles_then_stops(tmp_path: Path) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(store, call_id=f"call_{i}", nonce=nonce_of(i))
    chain = FakeChain(
        events_by_tx=[
            [
                log_charged(
                    provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(i)
                )
                for i in range(1, 4)
            ]
        ]
    )
    keeper = Keeper(
        store=store,
        chain=chain,
        wallet_for=wallet_for,
        batch_size=3,
        poll_interval=0.01,
        now=lambda: NOW,
    )
    keeper.start()
    for _ in range(200):  # 最多等 2s
        if settle_statuses(store).get("done"):
            break
        await asyncio.sleep(0.01)
    await keeper.stop()
    assert len(settle_statuses(store)["done"]) == 3
    assert keeper.status_snapshot()["running"] is False
    store.close()


# ---- status 端点与拉黑拦截（经 app 装配） ----------------------------------


async def test_status_endpoint_and_blacklist_gate(tmp_path: Path, settings: object) -> None:
    from app.core.config import Settings
    from tests.conftest import gateway_serve

    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(store, call_id=f"call_{i}", nonce=nonce_of(i))
    chain = FakeChain(
        events_by_tx=[
            [
                log_charged(
                    provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(i)
                )
                for i in range(1, 4)
            ]
        ]
    )
    keeper, _clock = make_keeper(store, chain)
    await keeper.run_cycle()

    keeper_settings = Settings(
        duckdb_path=":memory:",
        pay_vault_address=VAULT,
        chain_id=968,
        keeper_enabled=False,
    )
    async with gateway_serve(keeper_settings, keeper=keeper) as (client, _app):  # type: ignore[arg-type]
        resp = await client.get("/internal/keeper/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["cumulative_charged_raw"] == "30000"
        assert body["queue"]["done"] == 3
        assert body["last_batch"]["charged"] == 3

        keeper.blacklist(CONSUMER_A)
        blocked = await client.post(
            "/call/svc_translate_v1",
            json={"text": "hello"},
            headers={
                "X-Api-Key": API_KEY,
                "X-PAYMENT": make_x_payment_header(nonce="0x" + "77" * 32),
            },
        )
        assert blocked.status_code == 402
        assert blocked.json()["code"] == "bad_debt"
    store.close()


def test_inert_keeper_without_chain(tmp_path: Path) -> None:
    """未装配 chain（keeper_enabled=False）：不结算，仅黑名单/状态视图可用。"""
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    keeper = Keeper(store=store)
    assert keeper.blacklisted(CONSUMER_A) is False
    snapshot = keeper.status_snapshot()
    assert snapshot["enabled"] is False
    assert snapshot["queue"]["pending"] == 0
    store.close()


# ---- BotChainSettleChain（respx 方法级装饰器 / _fetch_receipt 注入） --------

BOTCHAIN = "http://botchain.test"
OPERATOR = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a"


def _chain_client() -> BotChainSettleChain:
    return BotChainSettleChain(
        base_url=BOTCHAIN,
        rpc_url="http://rpc.test/",
        pay_vault=VAULT,
        operator_address=OPERATOR,
        http=httpx.AsyncClient(),
    )


@respx.mock
async def test_client_submit_batch_ok() -> None:
    route = respx.post(f"{BOTCHAIN}/api/v1/contracts/send").mock(
        return_value=httpx.Response(
            200, json={"dry_run": False, "tx_hash": "0xabc", "status": 1, "block_number": 1}
        )
    )
    client = _chain_client()
    calls = [
        {
            "provider": PROVIDER_WALLET,
            "auth": {
                "from": CONSUMER_A,
                "to": VAULT,
                "value": 10000,
                "validAfter": 0,
                "validBefore": VALID_BEFORE_FAR,
                "nonce": nonce_of(1),
            },
            "v": 27,
            "r": "0x" + "11" * 32,
            "s": "0x" + "22" * 32,
        }
    ]
    tx_hash = await client.submit_batch(calls)
    assert tx_hash == "0xabc"
    sent = json.loads(route.calls.last.request.content)
    assert sent["from_address"] == OPERATOR
    assert sent["to"] == VAULT
    assert sent["method"] == "chargeWithSigBatch"
    assert sent["dry_run"] is False
    assert sent["args"] == [calls]
    assert any(e.get("name") == "chargeWithSigBatch" for e in sent["abi"])
    await client.aclose()


@respx.mock
async def test_client_submit_batch_error_passes_detail() -> None:
    respx.post(f"{BOTCHAIN}/api/v1/contracts/send").mock(
        return_value=httpx.Response(
            409, json={"error": "tx_reverted", "detail": "NotOperator()", "code": "reverted"}
        )
    )
    client = _chain_client()
    with pytest.raises(SettleChainError) as exc:
        await client.submit_batch([])
    assert "NotOperator" in str(exc.value)
    await client.aclose()


@respx.mock
async def test_client_receipt_status() -> None:
    respx.get(f"{BOTCHAIN}/api/v1/tx/0xabc").mock(
        return_value=httpx.Response(
            200, json={"found": True, "tx_hash": "0xabc", "status": 1, "block_number": 9}
        )
    )
    client = _chain_client()
    assert await client.receipt_status("0xabc") == 1
    await client.aclose()


@respx.mock
async def test_client_is_nonce_used() -> None:
    route = respx.post(f"{BOTCHAIN}/api/v1/contracts/call").mock(
        return_value=httpx.Response(200, json={"to": VAULT, "method": "usedNonces", "result": True})
    )
    client = _chain_client()
    assert await client.is_nonce_used(nonce_of(7)) is True
    sent = json.loads(route.calls.last.request.content)
    assert sent["method"] == "usedNonces" and sent["args"] == [nonce_of(7)]
    await client.aclose()


async def test_client_receipt_logs_via_injected_receipt() -> None:
    class ProbingClient(BotChainSettleChain):
        async def _fetch_receipt(self, tx_hash: str) -> dict[str, Any]:
            return {
                "status": 1,
                "logs": [
                    log_charged(
                        provider=PROVIDER_WALLET,
                        from_=CONSUMER_A,
                        value=10000,
                        nonce=nonce_of(3),
                    )
                ],
            }

    client = ProbingClient(
        base_url=BOTCHAIN,
        rpc_url="http://rpc.test/",
        pay_vault=VAULT,
        operator_address=OPERATOR,
    )
    logs = await client.receipt_logs("0xabc")
    events = decode_charge_events(logs, VAULT)
    assert events[0].kind == "charged" and events[0].value == 10000
    await client.aclose()


# ---- BotChainSettleChain api 模式（httpx.MockTransport 假 bot-chain-api） ----


def _api_raw_log(log: dict[str, Any], *, tx_hash: str, block_number: int = 9) -> dict[str, Any]:
    """/contracts/logs 原始日志形态（bot-chain-api contracts._raw_log：snake_case + 0x 归一）。"""
    return {
        "address": log["address"],
        "topics": log["topics"],
        "data": log["data"],
        "block_number": block_number,
        "block_hash": "0x" + "00" * 32,
        "transaction_hash": tx_hash,
        "transaction_index": 0,
        "log_index": 0,
        "removed": False,
    }


def _api_chain(handler: Any) -> BotChainSettleChain:
    """api 模式客户端：httpx.MockTransport 直供（零网络、零 web3）。"""
    return BotChainSettleChain(
        base_url=BOTCHAIN,
        rpc_url="http://rpc.test/",
        pay_vault=VAULT,
        operator_address=OPERATOR,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        mode="api",
    )


async def test_client_api_mode_receipt_logs_filters_by_tx() -> None:
    """/tx 定位块 → /contracts/logs 块窗 → 按 transaction_hash 过滤（同块他笔剔除）。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/v1/tx/0xabc":
            return httpx.Response(
                200, json={"found": True, "tx_hash": "0xabc", "status": 1, "block_number": 9}
            )
        assert request.url.path == "/api/v1/contracts/logs"
        assert dict(request.url.params) == {
            "address": VAULT,
            "from_block": "9",
            "to_block": "9",
            "limit": "5000",
        }
        mine = _api_raw_log(
            log_charged(provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(3)),
            tx_hash="0xabc",
        )
        other_tx = _api_raw_log(
            log_charged(provider=PROVIDER_WALLET, from_=CONSUMER_B, value=7, nonce=nonce_of(4)),
            tx_hash="0x" + "ff" * 32,
        )
        return httpx.Response(
            200,
            json={
                "address": VAULT,
                "topic0": None,
                "from_block": 9,
                "to_block": 9,
                "count": 2,
                "truncated": False,
                "logs": [mine, other_tx],
            },
        )

    client = _api_chain(handler)
    logs = await client.receipt_logs("0xabc")
    assert seen == ["/api/v1/tx/0xabc", "/api/v1/contracts/logs"]
    events = decode_charge_events(logs, VAULT)
    assert len(events) == 1 and events[0].kind == "charged"
    assert events[0].value == 10000 and events[0].nonce == nonce_of(3)
    await client.aclose()


async def test_client_api_mode_receipt_logs_tx_missing() -> None:
    """/tx 未找到（found=False）→ SettleChainError（行保持 pending 待重试）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/tx/0xabc"
        return httpx.Response(200, json={"found": False, "tx_hash": "0xabc"})

    client = _api_chain(handler)
    with pytest.raises(SettleChainError, match="尚未上链"):
        await client.receipt_logs("0xabc")
    await client.aclose()


async def test_client_api_mode_logs_rejected() -> None:
    """/contracts/logs 非 200 → SettleChainError 带状态码与响应体片段。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/tx/0xabc":
            return httpx.Response(
                200, json={"found": True, "tx_hash": "0xabc", "status": 1, "block_number": 9}
            )
        return httpx.Response(500, text="boom")

    client = _api_chain(handler)
    with pytest.raises(SettleChainError, match=r"contracts/logs 被拒\(500\)"):
        await client.receipt_logs("0xabc")
    await client.aclose()


def test_client_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="direct"):
        BotChainSettleChain(
            base_url=BOTCHAIN,
            rpc_url="http://rpc.test/",
            pay_vault=VAULT,
            operator_address=OPERATOR,
            mode="proxy",
        )


def test_keeper_chain_mode_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 唯一入口：缺省 direct；COINCALL_KEEPER_CHAIN_MODE=api 生效；非法值拒绝启动。"""
    from pydantic import ValidationError

    from app.core.config import Settings

    assert Settings().keeper_chain_mode == "direct"
    monkeypatch.setenv("COINCALL_KEEPER_CHAIN_MODE", "api")
    assert Settings().keeper_chain_mode == "api"
    monkeypatch.setenv("COINCALL_KEEPER_CHAIN_MODE", "proxy")
    with pytest.raises(ValidationError):
        Settings()


async def test_client_direct_mode_regression_no_api_calls() -> None:
    """direct（缺省）回归：receipt_logs 仍走 _fetch_receipt（web3），零 bot-chain-api 触达。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"direct 模式不应触达 bot-chain-api: {request.url}")

    class ProbingClient(BotChainSettleChain):
        async def _fetch_receipt(self, tx_hash: str) -> dict[str, Any]:
            return {
                "status": 1,
                "logs": [
                    log_charged(
                        provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(3)
                    )
                ],
            }

    client = ProbingClient(
        base_url=BOTCHAIN,
        rpc_url="http://rpc.test/",
        pay_vault=VAULT,
        operator_address=OPERATOR,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    logs = await client.receipt_logs("0xabc")
    events = decode_charge_events(logs, VAULT)
    assert events[0].kind == "charged" and events[0].value == 10000
    await client.aclose()


# ---- api 模式经 Keeper 全链路（MockTransport 假 bot-chain-api 四端点） ------


async def test_keeper_api_mode_happy_path(tmp_path: Path) -> None:
    """api 模式 happy path：send → /tx 回执 → 块窗日志 → 3 笔 settle=done + calls=settled。"""
    from app.modules.calls import CallStatus

    tx_hash = "0x" + "ab" * 32

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/contracts/call":  # 重启首轮 usedNonces 探测
            return httpx.Response(200, json={"to": VAULT, "method": "usedNonces", "result": False})
        if path == "/api/v1/contracts/send":
            sent = json.loads(request.content)
            assert sent["from_address"] == OPERATOR and sent["dry_run"] is False
            return httpx.Response(
                200,
                json={
                    "dry_run": False,
                    "tx_hash": tx_hash,
                    "status": 1,
                    "block_number": 9,
                    "gas_used": 120000,
                    "effective_gas_price_wei": "20000000000",
                    "from_address": OPERATOR,
                    "to_address": VAULT,
                    "contract_address": None,
                },
            )
        if path == f"/api/v1/tx/{tx_hash}":
            return httpx.Response(
                200, json={"found": True, "tx_hash": tx_hash, "status": 1, "block_number": 9}
            )
        assert path == "/api/v1/contracts/logs"
        logs = [
            _api_raw_log(
                log_charged(
                    provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(i)
                ),
                tx_hash=tx_hash,
            )
            for i in range(1, 4)
        ]
        return httpx.Response(
            200,
            json={
                "address": VAULT,
                "topic0": None,
                "from_block": 9,
                "to_block": 9,
                "count": 3,
                "truncated": False,
                "logs": logs,
            },
        )

    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(store, call_id=f"call_{i}", nonce=nonce_of(i))
    chain = _api_chain(handler)
    keeper = Keeper(store=store, chain=chain, wallet_for=wallet_for, batch_size=3, now=lambda: NOW)
    report = await keeper.run_cycle()
    assert report["flushed"] is True and report["charged"] == 3
    assert settle_statuses(store)["done"] == ["call_1", "call_2", "call_3"]
    assert keeper.metrics.cumulative_charged_count == 3
    assert store.get_call("call_1")["status"] == CallStatus.SETTLED
    await chain.aclose()
    store.close()


async def test_keeper_api_mode_send_failure_keeps_pending(tmp_path: Path) -> None:
    """api 模式 send 被拒（409 revert）→ SettleChainError；3 笔保持 pending 待下轮。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/contracts/call":
            return httpx.Response(200, json={"to": VAULT, "method": "usedNonces", "result": False})
        assert request.url.path == "/api/v1/contracts/send"
        return httpx.Response(
            409, json={"error": "tx_reverted", "detail": "NotOperator()", "code": "reverted"}
        )

    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(store, call_id=f"call_{i}", nonce=nonce_of(i))
    chain = _api_chain(handler)
    keeper = Keeper(store=store, chain=chain, wallet_for=wallet_for, batch_size=3, now=lambda: NOW)
    with pytest.raises(SettleChainError, match="chargeWithSigBatch 被拒"):
        await keeper.run_cycle()
    assert settle_statuses(store)["pending"] == ["call_1", "call_2", "call_3"]
    assert keeper.metrics.last_error is not None
    await chain.aclose()
    store.close()


# ---- 补充：resolver / 常驻循环韧性 / 边缘分支 ------------------------------


async def test_provider_wallet_resolver_override_and_manifest() -> None:
    from app.modules.calls import CallStatus
    from app.modules.keeper import provider_wallet_resolver
    from tests.conftest import FakeManifests, make_manifest

    store = CallStore(":memory:")
    seed_settle(store, call_id="call_ovr", agent_id=137, nonce=nonce_of(1))
    seed_settle(store, call_id="call_mani", agent_id=999, nonce=nonce_of(2))
    store.mark_call("call_mani", CallStatus.SUCCESS)
    manifests = FakeManifests({"svc_translate_v1": make_manifest()})

    resolver = provider_wallet_resolver({"137": CONSUMER_B}, store, manifests)
    assert await resolver({"call_id": "call_ovr", "provider_token_id": 137}) == CONSUMER_B
    hit = await resolver({"call_id": "call_mani", "provider_token_id": 999})
    assert hit is not None and hit.lower() == PROVIDER_WALLET.lower()
    assert await resolver({"call_id": "call_missing", "provider_token_id": 999}) is None
    store.close()


async def test_run_loop_survives_cycle_errors(tmp_path: Path) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(store, call_id=f"call_{i}", nonce=nonce_of(i))
    broken = FakeChain(submit_error=SettleChainError("网络抖动"))
    healthy_events = [
        log_charged(provider=PROVIDER_WALLET, from_=CONSUMER_A, value=10000, nonce=nonce_of(i))
        for i in range(1, 4)
    ]
    keeper = Keeper(
        store=store,
        chain=broken,
        wallet_for=wallet_for,
        batch_size=3,
        poll_interval=0.01,
        now=lambda: NOW,
    )
    keeper.start()
    await asyncio.sleep(0.05)
    assert keeper.metrics.last_error is not None  # 周期失败被吞掉，任务仍活着
    assert keeper.status_snapshot()["running"] is True

    broken.submit_error = None  # 网络恢复：同任务继续把批结掉
    broken.events_by_tx = [healthy_events]
    for _ in range(200):
        if settle_statuses(store).get("done"):
            break
        await asyncio.sleep(0.01)
    await keeper.stop()
    assert len(settle_statuses(store)["done"]) == 3
    store.close()


async def test_reverted_receipt_raises_and_keeps_pending(tmp_path: Path) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    for i in range(1, 4):
        seed_settle(store, call_id=f"call_{i}", nonce=nonce_of(i))

    class RevertedChain(FakeChain):
        async def receipt_status(self, tx_hash: str) -> int | None:
            return 0

    chain = RevertedChain(events_by_tx=[[]])
    keeper, _clock = make_keeper(store, chain)
    with pytest.raises(SettleChainError) as exc:
        await keeper.run_cycle()
    assert "status=0" in str(exc.value)
    assert len(settle_statuses(store)["pending"]) == 3
    store.close()


async def test_unknown_reason_marks_failed(tmp_path: Path) -> None:
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    seed_settle(store, call_id="call_x", nonce=nonce_of(1))
    chain = FakeChain(
        events_by_tx=[
            [log_failed(provider=PROVIDER_WALLET, from_=CONSUMER_A, reason="future_reason")]
        ]
    )
    keeper, _clock = make_keeper(store, chain, batch_size=1)
    report = await keeper.run_cycle()
    assert report["failed"] == 1
    assert settle_statuses(store).get("failed") == ["call_x"]
    store.close()


async def test_unmatched_events_are_ignored_rows_keep_pending(tmp_path: Path) -> None:
    """Charged 的 nonce 对不上 / ChargeFailed 的 from 对不上：警告并保持 pending。"""
    store = CallStore(str(tmp_path / "gateway.duckdb"))
    seed_settle(store, call_id="call_1", nonce=nonce_of(1))
    chain = FakeChain(
        events_by_tx=[
            [
                log_charged(
                    provider=PROVIDER_WALLET,
                    from_=CONSUMER_A,
                    value=10000,
                    nonce=nonce_of(99),  # 不在批内
                ),
                log_failed(provider=PROVIDER_WALLET, from_=CONSUMER_B, reason="nonce_used"),
            ]
        ]
    )
    keeper, _clock = make_keeper(store, chain, batch_size=1)
    report = await keeper.run_cycle()
    assert report["charged"] == 0 and report["expired"] == 0
    assert settle_statuses(store)["pending"] == ["call_1"]  # 无事件归属 → 不动
    store.close()


@respx.mock
async def test_client_submit_missing_tx_hash_raises() -> None:
    respx.post(f"{BOTCHAIN}/api/v1/contracts/send").mock(
        return_value=httpx.Response(200, json={"dry_run": False, "status": 1})
    )
    client = _chain_client()
    with pytest.raises(SettleChainError) as exc:
        await client.submit_batch([])
    assert "tx_hash" in str(exc.value)
    await client.aclose()
