"""M1 链信息：info/gas/stats/blocks/health。"""

import time

from fastapi import APIRouter
from hexbytes import HexBytes
from httpx import Client
from pydantic import BaseModel
from web3 import Web3
from web3.exceptions import Web3Exception

from app.core.deps import BundlerDep, ChainDep, ExplorerDep, ExplorerHttpDep, Web3Dep
from app.core.errors import ChainError

router = APIRouter(prefix="/chain", tags=["M1 chain"])

BLOCK_INTERVAL_SAMPLE = 10
RECENT_BLOCKS_FOR_INTERVAL = 10
HEALTH_PROBE_TIMEOUT_S = 5.0
HTTP_OK = 200


class ChainInfo(BaseModel):
    network: str
    chain_id: int
    client_version: str
    block_number: int
    block_interval_s: float
    explorer_url: str
    rpc_url: str


class GasInfo(BaseModel):
    gas_price_wei: int
    gas_price_gwei: int
    base_fee_per_gas_wei: int
    transfer_cost_wei: int
    source: str


class ChainStats(BaseModel):
    total_transactions: int | None
    total_addresses: int | None
    transactions_today: int | None
    average_block_time_ms: float | None
    coin_price_usd: float | None


class BlockDetail(BaseModel):
    number: int
    hash: str
    parent_hash: str
    timestamp: int
    tx_count: int
    tx_hashes: list[str]
    gas_used: int
    miner: str


class ChannelHealth(BaseModel):
    ok: bool
    latency_ms: int
    detail: str


class HealthReport(BaseModel):
    ok: bool
    channels: dict[str, ChannelHealth]


@router.get("/info", response_model=ChainInfo)
def chain_info(w3: Web3Dep, chain: ChainDep) -> ChainInfo:
    tip = w3.eth.block_number
    interval = _mean_block_interval(w3, tip)
    return ChainInfo(
        network=chain.network.value,
        chain_id=w3.eth.chain_id,
        client_version=w3.client_version,
        block_number=tip,
        block_interval_s=round(interval, 3),
        explorer_url=chain.explorer_url,
        rpc_url=chain.rpc_url,
    )


@router.get("/gas", response_model=GasInfo)
def chain_gas(w3: Web3Dep) -> GasInfo:
    gas_price = w3.eth.gas_price  # 实测恒 20 gwei，仍从链上读以防未来调整
    block = w3.eth.get_block("latest")
    return GasInfo(
        gas_price_wei=gas_price,
        gas_price_gwei=gas_price // 10**9,
        base_fee_per_gas_wei=int(block.get("baseFeePerGas", 0) or 0),
        transfer_cost_wei=21000 * gas_price,
        source="eth_gasPrice + latest.baseFeePerGas",
    )


@router.get("/stats", response_model=ChainStats)
def chain_stats(explorer: ExplorerDep) -> ChainStats:
    raw = explorer.stats()

    def _int(field: str) -> int | None:
        value = raw.get(field)
        return int(value) if value is not None else None

    def _float(field: str) -> float | None:
        value = raw.get(field)
        return float(value) if value is not None else None

    return ChainStats(
        total_transactions=_int("total_transactions"),
        total_addresses=_int("total_addresses"),
        transactions_today=_int("transactions_today"),
        average_block_time_ms=_float("average_block_time"),
        coin_price_usd=_float("coin_price"),
    )


@router.get("/blocks/{number}", response_model=BlockDetail)
def block_detail(w3: Web3Dep, number: int) -> BlockDetail:
    try:
        raw = w3.eth.get_block(number)
    except Web3Exception as exc:
        msg = f"取块失败: {number} ({exc})"
        raise ChainError(msg) from exc
    txs = raw.get("transactions", [])
    hashes = [t if isinstance(t, str) else _hex_of(t) for t in txs]
    return BlockDetail(
        number=int(raw["number"]),
        hash=_hex_of(raw["hash"]),
        parent_hash=_hex_of(raw["parentHash"]),
        timestamp=int(raw["timestamp"]),
        tx_count=len(hashes),
        tx_hashes=hashes,
        gas_used=int(raw.get("gasUsed", 0)),
        miner=raw.get("miner", ""),
    )


@router.get("/health", response_model=HealthReport)
def chain_health(
    w3: Web3Dep, explorer_http: ExplorerHttpDep, bundler_http: BundlerDep, chain: ChainDep
) -> HealthReport:
    channels = {
        "rpc": _probe_rpc(w3, chain.chain_id),
        "bundler": _probe_bundler(bundler_http, chain.chain_id),
        "explorer": _probe_explorer(explorer_http),
    }
    return HealthReport(ok=all(c.ok for c in channels.values()), channels=channels)


def _mean_block_interval(w3: Web3, tip: int) -> float:
    try:
        blocks = [w3.eth.get_block(tip - i) for i in range(RECENT_BLOCKS_FOR_INTERVAL)]
        timestamps = [int(b["timestamp"]) for b in reversed(blocks)]
        diffs = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
        return sum(diffs) / len(diffs) if diffs else 0.0
    except Web3Exception:
        return 0.0


def _probe_rpc(w3: Web3, expected_chain_id: int) -> ChannelHealth:
    """rpc 通道：连通 且 chainId 与部署配置一致才算绿（网络/代理误配防线）。"""
    started = time.perf_counter()
    try:
        if not w3.is_connected():
            return ChannelHealth(ok=False, latency_ms=_ms_since(started), detail="not connected")
        got = w3.eth.chain_id
        if got != expected_chain_id:
            return ChannelHealth(
                ok=False,
                latency_ms=_ms_since(started),
                detail=f"chainId mismatch: rpc={got} expect={expected_chain_id}",
            )
        return ChannelHealth(
            ok=True, latency_ms=_ms_since(started), detail=f"connected, chainId={got}"
        )
    except Exception as exc:  # 探活必须吞掉一切异常转为红灯
        return ChannelHealth(ok=False, latency_ms=_ms_since(started), detail=str(exc)[:120])


def _probe_bundler(client: Client, expected_chain_id: int) -> ChannelHealth:
    started = time.perf_counter()
    try:
        resp = client.post(
            "",
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
            timeout=HEALTH_PROBE_TIMEOUT_S,
        )
        body = resp.json()
        got = int(body.get("result", "0x0"), 16)
        ok = got == expected_chain_id
        detail = f"chainId={got}"
    except Exception as exc:  # 探活必须吞掉一切异常转为红灯
        ok, detail = False, str(exc)[:120]
    return ChannelHealth(ok=ok, latency_ms=_ms_since(started), detail=detail)


def _probe_explorer(client: Client) -> ChannelHealth:
    started = time.perf_counter()
    try:
        resp = client.get("/stats", timeout=HEALTH_PROBE_TIMEOUT_S)
        ok = resp.status_code == HTTP_OK
        detail = f"HTTP {resp.status_code}"
    except Exception as exc:  # 探活必须吞掉一切异常转为红灯
        ok, detail = False, str(exc)[:120]
    return ChannelHealth(ok=ok, latency_ms=_ms_since(started), detail=detail)


def _hex_of(value: object) -> str:
    """块哈希字段（HexBytes|TxData 并集）→ 0x 串。"""
    return Web3.to_hex(HexBytes(value))  # type: ignore[arg-type]  # 运行时均为 hex 字节串


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
