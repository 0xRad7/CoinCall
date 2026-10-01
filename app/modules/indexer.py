"""M9 索引器：blocks/logs/token-transfers/agent-identities 增量入库 + 水位 + SSE 订阅。"""

import asyncio
import json
import time
from typing import Any, cast

from eth_typing import ChecksumAddress, HexStr
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from hexbytes import HexBytes
from pydantic import BaseModel, Field
from web3 import Web3
from web3.exceptions import Web3Exception
from web3.types import FilterParams

from app.core.abis.erc20 import TRANSFER_TOPIC
from app.core.abis.erc8004 import IDENTITY_REGISTRY_ABI
from app.core.chains import ChainSpec
from app.core.deps import ChainDep
from app.core.errors import ChainError
from app.core.log import get_logger
from app.core.rpc import contract_at
from app.storage.duckdb import DuckStore

logger = get_logger(__name__)

router = APIRouter(prefix="/indexer", tags=["M9 indexer"])

DEFAULT_WINDOW = 100
GETLOGS_WINDOW = 5000
REORG_DEPTH = 64  # 04 篇：最近 64 块视为未确认，sync 时回退重扫
SSE_POLL_S = 1.0
TOPIC_COUNT_ERC20 = 3
TOPIC_COUNT_ERC721 = 4
KIND_ERC20 = 20
KIND_ERC721 = 721
SSE_MAX_S = 300


class SyncRequest(BaseModel):
    window: int = Field(
        default=DEFAULT_WINDOW, ge=1, le=GETLOGS_WINDOW, description="从当前水位/链尖回看的块数"
    )
    from_block: int | None = None
    to_block: int | None = None


class SyncResult(BaseModel):
    stream: str
    from_block: int
    to_block: int
    synced_blocks: int
    rows: int


class StatusView(BaseModel):
    network: str
    counts: dict[str, int | None]


class IndexerService:
    """同步内核（模块内实现；storage 经 DuckStore 注入）。"""

    def __init__(self, w3: Web3, store: DuckStore, chain: ChainSpec) -> None:
        self._w3 = w3
        self._store = store
        self._chain = chain

    # ---- blocks ------------------------------------------------------------
    def sync_blocks(self, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
        tip = self._w3.eth.block_number
        watermark = self._store.get_watermark("blocks")
        start = _window_start(tip, watermark, window)
        count = 0
        for number in range(start, tip + 1):
            raw = self._w3.eth.get_block(number)
            txs = raw.get("transactions", [])
            self._store.upsert_block(
                number=number,
                hash=Web3.to_hex(raw["hash"]),
                timestamp=int(raw["timestamp"]),
                tx_count=len(txs),
                gas_used=int(raw.get("gasUsed", 0)),
                miner=raw.get("miner", ""),
            )
            for idx, tx_hash in enumerate(txs):
                hex_hash = (
                    tx_hash if isinstance(tx_hash, str) else Web3.to_hex(cast(HexBytes, tx_hash))
                )
                self._ingest_transaction(hex_hash, idx)
            count += 1
        self._store.set_watermark("blocks", tip)
        return {
            "stream": "blocks",
            "from_block": start,
            "to_block": tip,
            "synced_blocks": count,
            "rows": count,
        }

    def _ingest_transaction(self, tx_hash: str, tx_index: int) -> None:
        receipt = self._w3.eth.get_transaction_receipt(HexStr(tx_hash))
        tx = self._w3.eth.get_transaction(HexStr(tx_hash))
        self._store.upsert_transaction(
            hash=tx_hash,
            block_number=int(receipt.get("blockNumber", 0)),
            tx_index=tx_index,
            from_addr=receipt.get("from", ""),
            to_addr=receipt.get("to") or tx.get("to"),
            value_wei=int(tx.get("value", 0)),
            gas_used=int(receipt.get("gasUsed", 0)),
            status=int(receipt.get("status", 0)),
            tx_type=int(tx.get("type", 0)),
        )

    # ---- logs / token_transfers -------------------------------------------
    def sync_logs(
        self, window: int = DEFAULT_WINDOW, address: str | None = None, topic0: str | None = None
    ) -> dict[str, Any]:
        tip = self._w3.eth.block_number
        watermark = self._store.get_watermark("logs")
        start = _window_start(tip, watermark, window)
        try:
            criteria: dict[str, Any] = {"fromBlock": start, "toBlock": tip}
            if address:
                criteria["address"] = cast(ChecksumAddress, Web3.to_checksum_address(address))
            if topic0:
                criteria["topics"] = [topic0]
            logs = self._w3.eth.get_logs(cast(FilterParams, criteria))
        except Web3Exception as exc:
            msg = f"getLogs 失败: {exc}"
            raise ChainError(msg) from exc
        rows = 0
        for log in logs:
            topics = [str(Web3.to_hex(t)) for t in log["topics"]]
            self._store.upsert_log(
                log_index=int(log["logIndex"]),
                block_number=int(log["blockNumber"]),
                tx_hash=Web3.to_hex(log["transactionHash"]),
                address=log["address"],
                topic0=topics[0] if topics else "",
                topics=topics,
                data=Web3.to_hex(log["data"]),
            )
            rows += 1
        self._store.set_watermark("logs", tip)
        return {
            "stream": "logs",
            "from_block": start,
            "to_block": tip,
            "synced_blocks": tip - start + 1,
            "rows": rows,
        }

    def sync_token_transfers(self, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
        """Transfer 主题归一化：ERC20（3 topic）/ERC721（4 topic）分流入库。"""
        tip = self._w3.eth.block_number
        watermark = self._store.get_watermark("transfers")
        start = _window_start(tip, watermark, window)
        try:
            logs = self._w3.eth.get_logs(
                cast(FilterParams, {"fromBlock": start, "toBlock": tip, "topics": [TRANSFER_TOPIC]})
            )
        except Web3Exception as exc:
            msg = f"getLogs 失败: {exc}"
            raise ChainError(msg) from exc
        rows = 0
        for log in logs:
            topics = log["topics"]
            if len(topics) not in (TOPIC_COUNT_ERC20, TOPIC_COUNT_ERC721):
                continue
            kind = KIND_ERC20 if len(topics) == TOPIC_COUNT_ERC20 else KIND_ERC721
            if kind == KIND_ERC721:
                # ERC721 Transfer: from,to indexed；tokenId 在 topic3，data 空
                amount = int.from_bytes(topics[3], "big")
            else:
                amount = int.from_bytes(log["data"], "big")
            self._store.upsert_token_transfer(
                tx_hash=Web3.to_hex(log["transactionHash"]),
                log_index=int(log["logIndex"]),
                block_number=int(log["blockNumber"]),
                token=log["address"],
                token_kind=kind,
                from_addr=Web3.to_hex(topics[1][-20:]),
                to_addr=Web3.to_hex(topics[2][-20:]),
                amount=amount,
            )
            rows += 1
        self._store.set_watermark("transfers", tip)
        return {
            "stream": "transfers",
            "from_block": start,
            "to_block": tip,
            "synced_blocks": tip - start + 1,
            "rows": rows,
        }

    # ---- agent_identities（ERC-8004 对账）-----------------------------------
    def sync_agent_identities(self, window: int = GETLOGS_WINDOW) -> dict[str, int | str]:
        registry = self._chain.contracts.identity_registry
        tip = self._w3.eth.block_number
        watermark = self._store.get_watermark("identities")
        start = _window_start(tip, watermark, window)
        try:
            logs = self._w3.eth.get_logs(
                cast(
                    FilterParams,
                    {
                        "fromBlock": start,
                        "toBlock": tip,
                        "address": registry,
                        "topics": [TRANSFER_TOPIC],
                    },
                )
            )
        except Web3Exception as exc:
            msg = f"getLogs 失败: {exc}"
            raise ChainError(msg) from exc
        contract = contract_at(self._w3, registry, IDENTITY_REGISTRY_ABI)
        rows = 0
        for log in logs:
            token_id = int.from_bytes(log["topics"][3], "big")
            owner = Web3.to_hex(log["topics"][2][-20:])
            try:
                uri = contract.functions.tokenURI(token_id).call()
                wallet = contract.functions.getAgentWallet(token_id).call()
            except Web3Exception:
                uri, wallet = "", ""
            self._store.upsert_agent_identity(
                token_id=token_id,
                owner=owner,
                agent_wallet=wallet,
                token_uri=uri,
                minted_block=int(log["blockNumber"]),
            )
            rows += 1
        self._store.set_watermark("identities", tip)
        return {
            "stream": "identities",
            "from_block": start,
            "to_block": tip,
            "synced_blocks": tip - start + 1,
            "rows": rows,
        }


def _window_start(tip: int, watermark: int | None, window: int) -> int:
    """水位 - 64 块重扫窗口（reorg 兜底）与 window 取较大回看。"""
    if watermark is None:
        return max(0, tip - window + 1)
    return max(0, min(tip - window + 1, watermark - REORG_DEPTH))


def _indexer(request: Request) -> IndexerService:
    return request.app.state.indexer


@router.post("/sync/blocks", response_model=SyncResult)
def sync_blocks(request: SyncRequest, raw_request: Request) -> SyncResult:
    result = _indexer(raw_request).sync_blocks(request.window)
    return SyncResult(**result)


@router.post("/sync/logs", response_model=SyncResult)
def sync_logs(request: SyncRequest, raw_request: Request) -> SyncResult:
    result = _indexer(raw_request).sync_logs(request.window)
    return SyncResult(**result)


@router.post("/sync/token-transfers", response_model=SyncResult)
def sync_token_transfers(request: SyncRequest, raw_request: Request) -> SyncResult:
    result = _indexer(raw_request).sync_token_transfers(request.window)
    return SyncResult(**result)


@router.post("/sync/agent-identities", response_model=SyncResult)
def sync_agent_identities(request: SyncRequest, raw_request: Request) -> SyncResult:
    result = _indexer(raw_request).sync_agent_identities(request.window)
    return SyncResult(**result)


@router.get("/status", response_model=StatusView)
def indexer_status(chain: ChainDep, request: Request) -> StatusView:
    store: DuckStore = request.app.state.store
    return StatusView(network=chain.network.value, counts=store.status())


@router.get("/subscribe")
async def subscribe(request: Request, channel: str = "ch:blocks:testnet") -> StreamingResponse:
    """SSE 流：新块/新事件推送（Redis pub/sub；未配置 Redis 时轮询块高兜底）。"""
    pubsub_obj = getattr(request.app.state, "redis_pubsub", None)

    async def event_stream() -> Any:  # noqa: ANN401  # SSE 生成器
        deadline = time.time() + SSE_MAX_S
        last_block: int | None = None
        while time.time() < deadline:
            if pubsub_obj is not None:
                message = pubsub_obj.get_message(ignore_subscribe_messages=True, timeout=SSE_POLL_S)
                if message and message.get("data"):
                    yield f"event: message\ndata: {message['data']}\n\n"
                continue

            def _tip(w: Web3 = request.app.state.w3) -> int:
                return int(w.eth.block_number)

            block: int = await asyncio.to_thread(_tip)
            if last_block is None or block > last_block:
                last_block = block
                yield f"event: block\ndata: {json.dumps({'number': block})}\n\n"
            await asyncio.sleep(SSE_POLL_S)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
