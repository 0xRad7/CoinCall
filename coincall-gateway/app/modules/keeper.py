"""keeper 结算器（04 §3 / 09 P0-5）：settle_queue 攒批 → bot-chain-api 上链 → 回执分支。

架构（已冻结）：
- gateway 服务内常驻 asyncio 后台任务（DuckDB 单写者 A1/C-14 决定不能独立进程抢库）；
- 链上提交只经 coincall-bot-chain-api ``POST /contracts/call|send``（铁律 P1），
  from=operator（bot-chain-api keystore 代管，本仓不持有任何私钥）；
- 回执事件 Charged/ChargeFailed 用本仓 web3+ABI 副本本地解码（不依赖 explorer）。

链上通道两种模式（env ``COINCALL_KEEPER_CHAIN_MODE``，装配见 main._build_keeper）：
- ``direct``（缺省，现行为零变化）：submit/回执状态/usedNonces 走 bot-chain-api HTTP，
  回执日志（receipt_logs）由本仓自建 AsyncWeb3 直连 RPC 读取——DNS 污染环境下
  主网 rpc.botchain.ai 不可达（mainnet-readiness §3.2/§5-1 唯一必改代码项）；
- ``api``：日志也经 bot-chain-api（/tx/{hash} 定位块 → /contracts/logs 块窗原始日志
  → 本地按 transaction_hash 过滤），本仓零 web3 连接——出链流量全部由 bot-chain-api
  的 PROXY 分流兜底，keystore 签名路径（TxService.resolve_signer）也天然复用。

reason 分支（合约短码 → 队列状态）：
- Charged                          → settle_queue=done + calls=settled
- transfer_failed（nonce 未烧）    → 原签名重试一次 → 仍失败：calls=bad_debt + 拉黑消费者
- not_in_window / nonce_used       → expired（等于从未扣款）
- bad_signature / auth_to_mismatch → failed + ERROR 告警（网关侧验签已拦，出现即 bug）

kill-重启续批：状态只在事件解析后落库；重启首轮对 pending 笔做 usedNonces
只读探测——nonce 已烧 = 上次实际已 Charged，直接补记 done（不重放不丢单）。
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol, cast

import httpx
from eth_utils import keccak, to_checksum_address
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.types import FilterParams, HexStr, LogReceipt

from app.core.abis import charge_batch_abi, event_abi, view_abi
from app.modules.calls import CallStatus, CallStore, SettleStatus

logger = logging.getLogger("coincall.gateway.keeper")

#: 合约 PayVault.MAX_BATCH（上链单笔硬上限）
MAX_BATCH = 50
#: 空转轮询间隔（秒）
POLL_TICK_S = 1.0
#: 回执轮询（/contracts/send 本身阻塞等回执，这里只是兜底）
RECEIPT_POLL_ATTEMPTS = 30
RECEIPT_POLL_INTERVAL_S = 1.0
#: /contracts/send 服务端等回执 30s → 客户端超时放宽到 60s
HTTP_TIMEOUT_S = 60.0
HTTP_OK = 200
HTTP_NOT_FOUND = 404
#: api 模式块窗拉日志的单次上限（对齐 bot-chain-api contracts.py MAX_LOGS_LIMIT）
LOGS_FETCH_LIMIT = 5000

# ---- 审计 F-01 缓解：Charged 事件 ↔ settle_queue 对账（payvault-security-audit §2 前置 1）----

#: Charged 事件签名 topic0（eth_getLogs 服务端过滤，只拉 Charged 不拉 ChargeFailed）
CHARGED_TOPIC0 = "0x" + keccak(text="Charged(address,address,uint256,bytes32)").hex()
#: 对账块窗分页大小（对齐 bot-chain-api contracts.py GETLOGS_WINDOW_LIMIT=5000 块/窗）
RECONCILE_PAGE_BLOCKS = 5000
#: 二分找 cutoff 块时假设的块时间下界（秒）：真实链 interval ≥ 该值即窗口覆盖完整，
#: 搜索范围 = tip - window_s/MIN_BLOCK_TIME_S（防全链二分触达远古块头）
MIN_BLOCK_TIME_S = 0.1
#: 二分块时间戳探测次数上限（防异常链高 runaway；log2(2^64) 远够）
BLOCK_PROBE_LIMIT = 64

REASON_TRANSFER_FAILED = "transfer_failed"
REASON_NOT_IN_WINDOW = "not_in_window"
REASON_NONCE_USED = "nonce_used"
#: 等于从未扣款 → expired
EXPIRE_REASONS = frozenset({REASON_NOT_IN_WINDOW, REASON_NONCE_USED})
#: 网关侧验签已拦，出现即 bug → failed + ERROR 告警
ALARM_REASONS = frozenset({"bad_signature", "auth_to_mismatch"})
#: transfer_failed：原签名重试一次，仍失败即坏账
MAX_TRANSFER_ATTEMPTS = 2

_DECODER_ADDRESS = "0x0000000000000000000000000000000000000001"


class SettleChainError(Exception):
    """链上提交/回执/探测失败（行保持 pending，nonce 未烧可安全重试）。"""


@dataclass(frozen=True)
class ChargeEvent:
    """解码后的结算事件（Charged / ChargeFailed）。"""

    kind: str  # "charged" | "failed"
    provider: str
    from_: str
    value: int | None = None
    nonce: str | None = None
    reason: str | None = None


@lru_cache(maxsize=1)
def _decoder() -> Any:
    """无 provider 的 web3 合约壳：仅用于事件 ABI 解码，不触网。"""
    w3: AsyncWeb3 = AsyncWeb3()
    return w3.eth.contract(address=to_checksum_address(_DECODER_ADDRESS), abi=event_abi())


def decode_charge_events(logs: list[dict[str, Any]], pay_vault: str) -> list[ChargeEvent]:
    """按合约地址过滤日志并本地解码 Charged/ChargeFailed（零网络）。"""
    contract = _decoder()
    events: list[ChargeEvent] = []
    for log in logs:
        if str(log.get("address", "")).lower() != pay_vault.lower():
            continue  # 同笔交易里 MockUSDT Transfer 等外来日志
        for name in ("Charged", "ChargeFailed"):
            try:
                raw = contract.events[name]().process_log(cast(LogReceipt, log))
            except Exception as exc:  # 逐条试解码，不匹配即跳过（MismatchedABI 等）
                logger.debug("日志与 %s 不匹配: %s", name, exc)
                continue
            args = dict(raw["args"])
            if name == "Charged":
                nonce_arg = args["nonce"]
                nonce_hex = (
                    "0x" + nonce_arg.hex() if isinstance(nonce_arg, bytes) else str(nonce_arg)
                )
                events.append(
                    ChargeEvent(
                        kind="charged",
                        provider=str(args["provider"]),
                        from_=str(args["from"]),
                        value=int(args["value"]),
                        nonce=nonce_hex,
                    )
                )
            else:
                events.append(
                    ChargeEvent(
                        kind="failed",
                        provider=str(args["provider"]),
                        from_=str(args["from"]),
                        reason=str(args["reason"]),
                    )
                )
            break
    return events


class SettleChain(Protocol):
    """keeper 的链上通道（提交/回执/探测/对账取数；单测全 fake）。"""

    async def submit_batch(self, calls: list[dict[str, Any]]) -> str: ...

    async def receipt_status(self, tx_hash: str) -> int | None: ...

    async def receipt_logs(self, tx_hash: str) -> list[dict[str, Any]]: ...

    async def is_nonce_used(self, nonce: str) -> bool: ...

    async def latest_block(self) -> int: ...

    async def block_timestamp(self, block_number: int) -> int: ...

    async def charged_events(self, from_block: int, to_block: int) -> list[ChargeEvent]: ...


def _plain_log(log: Any) -> dict[str, Any]:
    """web3 回执日志 → 纯 dict（bytes 字段归一 hex；C-02 同源教训）。"""
    out = dict(log)
    out["topics"] = [
        "0x" + t.hex() if isinstance(t, bytes) else str(t) for t in (out.get("topics") or [])
    ]
    for key in ("data", "blockHash", "transactionHash"):
        val = out.get(key)
        if isinstance(val, bytes):
            out[key] = "0x" + val.hex()
    return out


def _receipt_log_shape(log: dict[str, Any]) -> dict[str, Any]:
    """bot-chain-api /contracts/logs 原始日志（snake_case）→ web3 回执日志形态（camelCase）。

    process_log 只认 camelCase 键（web3 v7 口径，与 _plain_log 产物同构）。
    """
    return {
        "address": str(log.get("address", "")),
        "topics": [str(t) for t in (log.get("topics") or [])],
        "data": str(log.get("data", "0x")),
        "blockNumber": int(log.get("block_number", 0)),
        "blockHash": str(log.get("block_hash", "")),
        "transactionHash": str(log.get("transaction_hash", "")),
        "transactionIndex": int(log.get("transaction_index", 0)),
        "logIndex": int(log.get("log_index", 0)),
        "removed": bool(log.get("removed", False)),
    }


class BotChainSettleChain:
    """真实通道：写经 bot-chain-api（operator 由其 keystore 签名），读回执按模式分流。

    mode="direct"（缺省）：回执日志走本仓 web3 直连 RPC（现行为零变化）；
    mode="api"：日志也经 bot-chain-api（/tx/{hash} 定位块 → /contracts/logs 块窗
    原始日志 → 本地按 transaction_hash 过滤），DNS 污染环境主网可达（§5-1）。
    """

    def __init__(
        self,
        *,
        base_url: str,
        rpc_url: str,
        pay_vault: str,
        operator_address: str,
        http: httpx.AsyncClient | None = None,
        mode: str = "direct",
    ) -> None:
        if mode not in ("direct", "api"):
            raise ValueError(f"keeper_chain_mode 非法: {mode!r}（期望 direct | api）")
        self.base_url = base_url.rstrip("/")
        self.pay_vault = pay_vault
        self.operator_address = operator_address
        self.mode = mode
        self._rpc_url = rpc_url
        # trust_env=False：bot-chain-api 是直连服务地址，C-07——macOS 系统代理会把
        # 127.0.0.1 请求劫持成 502 空体（live 实跑踩坑）
        self._http = http or httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, trust_env=False)
        self._owns_http = http is None
        self._w3: AsyncWeb3 | None = None

    # ---- 写：唯一上链路径（铁律 P1） ----

    async def submit_batch(self, calls: list[dict[str, Any]]) -> str:
        payload = {
            "from_address": self.operator_address,
            "to": self.pay_vault,
            "abi": charge_batch_abi(),
            "method": "chargeWithSigBatch",
            "args": [calls],
            "value_wei": "0",
            "dry_run": False,
        }
        try:
            resp = await self._http.post(f"{self.base_url}/api/v1/contracts/send", json=payload)
        except httpx.HTTPError as exc:
            raise SettleChainError(f"bot-chain-api 提交失败: {exc}") from exc
        if resp.status_code != HTTP_OK:
            raise SettleChainError(
                f"chargeWithSigBatch 被拒({resp.status_code}): {resp.text[:300]}"
            )
        body = resp.json()
        tx_hash = body.get("tx_hash")
        if not tx_hash:
            raise SettleChainError(f"提交响应缺 tx_hash: {body}")
        return str(tx_hash)

    async def receipt_status(self, tx_hash: str) -> int | None:
        body = await self._fetch_tx_status(tx_hash)
        return None if body is None else int(body["status"])

    async def receipt_logs(self, tx_hash: str) -> list[dict[str, Any]]:
        if self.mode == "api":
            return await self._receipt_logs_via_api(tx_hash)
        receipt = await self._fetch_receipt(tx_hash)
        return [_plain_log(log) for log in receipt.get("logs", [])]

    async def is_nonce_used(self, nonce: str) -> bool:
        payload = {
            "to": self.pay_vault,
            "abi": view_abi(),
            "method": "usedNonces",
            "args": [nonce],
        }
        try:
            resp = await self._http.post(f"{self.base_url}/api/v1/contracts/call", json=payload)
        except httpx.HTTPError as exc:
            raise SettleChainError(f"usedNonces 探测失败: {exc}") from exc
        if resp.status_code != HTTP_OK:
            raise SettleChainError(f"usedNonces 被拒({resp.status_code}): {resp.text[:200]}")
        return bool(resp.json().get("result"))

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
        if self._w3 is not None:
            with contextlib.suppress(Exception):
                await self._w3.provider.disconnect()

    async def _fetch_receipt(self, tx_hash: str) -> dict[str, Any]:
        """direct 模式单点回执读取（单测 override 此处即可零网络；C-02 模式）。"""
        w3 = self._ensure_w3()
        receipt = await w3.eth.get_transaction_receipt(HexStr(tx_hash))
        return dict(receipt)

    async def _fetch_tx_status(self, tx_hash: str) -> dict[str, Any] | None:
        """GET /api/v1/tx/{hash} → 回执摘要；未找到返回 None（等待方轮询用）。"""
        try:
            resp = await self._http.get(f"{self.base_url}/api/v1/tx/{tx_hash}")
        except httpx.HTTPError as exc:
            raise SettleChainError(f"回执查询失败: {exc}") from exc
        if resp.status_code == HTTP_NOT_FOUND:
            return None
        if resp.status_code != HTTP_OK:
            raise SettleChainError(f"回执查询被拒({resp.status_code}): {resp.text[:200]}")
        body = resp.json()
        if not body.get("found"):
            return None
        return body

    async def _receipt_logs_via_api(self, tx_hash: str) -> list[dict[str, Any]]:
        """api 模式日志读取（零 web3 连接）：

        /tx/{hash} 定位区块 → /contracts/logs?address&from_block&to_block 拉该块
        PayVault 原始日志 → 本地按 transaction_hash 过滤出本笔交易的日志。
        /contracts/logs 返回 snake_case 原始形态（bot-chain-api _raw_log），需转回
        web3 回执日志的 camelCase 口径，decode_charge_events 的 process_log 才认
        （snake 直喂会 MismatchedABI 静默跳过——单测锁定的实坑）。
        """
        body = await self._fetch_tx_status(tx_hash)
        if body is None or body.get("block_number") is None:
            raise SettleChainError(f"回执尚未上链（无块号，无法块窗拉日志）: {tx_hash}")
        block = int(body["block_number"])
        try:
            resp = await self._http.get(
                f"{self.base_url}/api/v1/contracts/logs",
                params={
                    "address": self.pay_vault,
                    "from_block": block,
                    "to_block": block,
                    "limit": LOGS_FETCH_LIMIT,
                },
            )
        except httpx.HTTPError as exc:
            raise SettleChainError(f"contracts/logs 拉取失败: {exc}") from exc
        if resp.status_code != HTTP_OK:
            raise SettleChainError(f"contracts/logs 被拒({resp.status_code}): {resp.text[:200]}")
        want = tx_hash.lower()
        logs = resp.json().get("logs", [])
        return [
            _receipt_log_shape(log)
            for log in logs
            if str(log.get("transaction_hash", "")).lower() == want
        ]

    # ---- 审计 F-01 对账取数 B：Charged 事件块窗读取（只读，不发交易） ----

    def _ensure_w3(self) -> AsyncWeb3:
        if self._w3 is None:
            w3 = AsyncWeb3(AsyncHTTPProvider(self._rpc_url))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)  # POA extraData 超 32B
            self._w3 = w3
        return self._w3

    async def latest_block(self) -> int:
        """链尖块号：direct=本仓 web3；api=bot-chain-api /chain/info.block_number。"""
        if self.mode == "api":
            try:
                resp = await self._http.get(f"{self.base_url}/api/v1/chain/info")
            except httpx.HTTPError as exc:
                raise SettleChainError(f"chain/info 拉取失败: {exc}") from exc
            if resp.status_code != HTTP_OK:
                raise SettleChainError(f"chain/info 被拒({resp.status_code}): {resp.text[:200]}")
            return int(resp.json()["block_number"])
        return int(await self._ensure_w3().eth.block_number)

    async def block_timestamp(self, block_number: int) -> int:
        """块时间戳（unix 秒）：direct=web3 get_block；api=/chain/blocks/{n}.timestamp。"""
        if self.mode == "api":
            try:
                resp = await self._http.get(f"{self.base_url}/api/v1/chain/blocks/{block_number}")
            except httpx.HTTPError as exc:
                raise SettleChainError(f"chain/blocks 拉取失败: {exc}") from exc
            if resp.status_code != HTTP_OK:
                raise SettleChainError(f"chain/blocks 被拒({resp.status_code}): {resp.text[:200]}")
            return int(resp.json()["timestamp"])
        block = await self._ensure_w3().eth.get_block(block_number)
        return int(block["timestamp"])

    async def charged_events(self, from_block: int, to_block: int) -> list[ChargeEvent]:
        """块窗 [from_block, to_block] 内 PayVault 的 Charged 事件（已解码；只读）。

        窗口按 RECONCILE_PAGE_BLOCKS 分页：api 模式 bot-chain-api 限 5000 块/窗
        （contracts.py GETLOGS_WINDOW_LIMIT），direct 模式同口径分页防节点端范围限流。
        """
        logs: list[dict[str, Any]] = []
        cursor = max(0, from_block)
        while cursor <= to_block:
            page_to = min(cursor + RECONCILE_PAGE_BLOCKS - 1, to_block)
            logs.extend(await self._charged_logs_page(cursor, page_to))
            cursor = page_to + 1
        events = decode_charge_events(logs, self.pay_vault)
        return [e for e in events if e.kind == "charged"]

    async def _charged_logs_page(self, from_block: int, to_block: int) -> list[dict[str, Any]]:
        """单页原始日志：api=/contracts/logs（snake→camel 复用 _receipt_log_shape）；
        direct=本仓 web3 eth_getLogs（_fetch_logs 单点，单测 override 零网络）。"""
        if self.mode == "api":
            try:
                resp = await self._http.get(
                    f"{self.base_url}/api/v1/contracts/logs",
                    params={
                        "address": self.pay_vault,
                        "from_block": from_block,
                        "to_block": to_block,
                        "topic0": CHARGED_TOPIC0,
                        "limit": LOGS_FETCH_LIMIT,
                    },
                )
            except httpx.HTTPError as exc:
                raise SettleChainError(f"contracts/logs 拉取失败: {exc}") from exc
            if resp.status_code != HTTP_OK:
                raise SettleChainError(
                    f"contracts/logs 被拒({resp.status_code}): {resp.text[:200]}"
                )
            return [_receipt_log_shape(log) for log in resp.json().get("logs", [])]
        return await self._fetch_logs(from_block, to_block)

    async def _fetch_logs(self, from_block: int, to_block: int) -> list[dict[str, Any]]:
        """direct 模式单点 eth_getLogs（单测 override 此处即可零网络；C-02 模式）。"""
        criteria = {
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": to_checksum_address(self.pay_vault),
            "topics": [HexStr(CHARGED_TOPIC0)],
        }
        raw = await self._ensure_w3().eth.get_logs(cast(FilterParams, criteria))
        return [_plain_log(entry) for entry in raw]


@dataclass
class KeeperMetrics:
    """可观测累计量（GET /internal/keeper/status 直读）。"""

    queue_depth_pending: int = 0
    cumulative_charged_raw: int = 0
    cumulative_charged_count: int = 0
    last_batch: dict[str, Any] | None = None
    last_error: str | None = None
    #: 最近一次 F-01 对账摘要（GET /internal/keeper/reconcile 按需触发时刷新）
    last_reconcile: dict[str, Any] | None = None


def settle_counts(store: CallStore) -> dict[str, int]:
    """settle_queue 各状态行数（四态零填充；走 store 单写锁，C-14 串行纪律）。"""
    counts = {status.value: 0 for status in SettleStatus}
    with store._lock:  # 复用 CallStore 写锁串行化同连接读写
        rows = store.conn.execute(
            "SELECT status, count(*) FROM settle_queue GROUP BY status"
        ).fetchall()
    counts.update({str(status): int(count) for status, count in rows})
    return counts


def _auth_of(row: dict[str, Any]) -> dict[str, Any]:
    auth = row["auth_json"]
    if isinstance(auth, str):
        auth = json.loads(auth)
    return dict(auth)


def reconcile_charged(
    rows: list[dict[str, Any]],
    wallets: Mapping[str, str | None],
    events: Sequence[ChargeEvent],
) -> dict[str, Any]:
    """审计 F-01 核心对账（纯函数）：settle 行 ↔ Charged 事件逐笔比对。

    口径 = 部署冒烟 charged_matches_queue 同款四元组 (provider, from, value, nonce)——
    nonce 是消费者授权唯一键（先按 nonce 归属），其余三元组不一致即 field_mismatch
    （F-01 正靶：签名不绑定收款方，provider 记错只有这里能抓到）。

    告警语义（ok=false 判据，避免在途/坏账常态误报）：
    - queue_only 中 status=done 的行：done ⇒ 链上必有 Charged（mark_done 只在 Charged
      事件或 usedNonces 已烧后落库），没有即队列/落库被污染；
    - chain_only 任何一笔：外部直调/operator 绕过网关的 Charged（含空投面）；
    - field_mismatch 任何一笔：nonce 对上但 provider/from/value 与队列不一致。
    pending/failed/expired 行链上无事件属预期（在途/坏账/作废），列入 queue_only
    明细但不翻 ok；pending 行的 nonce 已在链上（错过恢复探测）单列 pending_but_charged
    记数不告警。
    """
    by_nonce: dict[str, dict[str, Any]] = {}
    dup_nonces: list[str] = []
    for row in rows:
        auth = _auth_of(row)
        nonce = str(auth["nonce"]).lower()
        if nonce in by_nonce:
            dup_nonces.append(nonce)
            continue
        by_nonce[nonce] = {
            "call_id": str(row["call_id"]),
            "status": str(row["status"]),
            "from": str(auth["from"]),
            "value": int(auth["value"]),
        }
    charged = [e for e in events if e.kind == "charged" and e.nonce is not None]

    matched = 0
    chain_only: list[str] = []
    field_mismatch: list[dict[str, Any]] = []
    pending_but_charged: list[dict[str, str]] = []
    hit: set[str] = set()
    for event in charged:
        key = str(event.nonce).lower()
        entry = by_nonce.get(key)
        if entry is None:
            chain_only.append(str(event.nonce))
            continue
        hit.add(key)
        wallet = wallets.get(entry["call_id"])
        diff: dict[str, Any] = {}
        if entry["from"].lower() != event.from_.lower():
            diff["from"] = {"queue": entry["from"], "chain": event.from_}
        if wallet is not None and wallet.lower() != event.provider.lower():
            diff["provider"] = {"queue": wallet, "chain": event.provider}
        if event.value is not None and entry["value"] != int(event.value):
            diff["value"] = {"queue": entry["value"], "chain": int(event.value)}
        if diff:
            field_mismatch.append(
                {
                    "call_id": entry["call_id"],
                    "nonce": event.nonce,
                    "status": entry["status"],
                    "diff": diff,
                }
            )
            continue
        matched += 1
        if entry["status"] == SettleStatus.PENDING.value:
            pending_but_charged.append({"call_id": entry["call_id"], "nonce": str(event.nonce)})

    queue_only = [
        {"call_id": entry["call_id"], "nonce": nonce, "status": entry["status"]}
        for nonce, entry in sorted(by_nonce.items())
        if nonce not in hit
    ]
    ok = (
        not chain_only
        and not field_mismatch
        and not any(item["status"] == SettleStatus.DONE.value for item in queue_only)
    )
    return {
        "queue_rows": len(rows),
        "chain_events": len(charged),
        "matched": matched,
        "queue_only": queue_only,
        "chain_only": chain_only,
        "field_mismatch": field_mismatch,
        "pending_but_charged": pending_but_charged,
        "queue_dup_nonces": dup_nonces,
        "wallet_unresolved": sum(1 for wallet in wallets.values() if wallet is None),
        "ok": ok,
    }


class Keeper:
    """结算器：攒批（笔数/时间阈值）→ 上链 → 回执分支 → 状态落库。

    未装配 chain（keeper_enabled=False）时为惰性实例：不结算，
    黑名单与 status 视图仍可用（apikey 校验路径的 bad_debt 拦截依赖它）。
    """

    def __init__(
        self,
        *,
        store: CallStore,
        chain: SettleChain | None = None,
        wallet_for: "WalletResolver | None" = None,
        batch_size: int = 3,
        flush_interval: float = 30.0,
        poll_interval: float = POLL_TICK_S,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.chain = chain
        self._wallet_for = wallet_for
        self._batch_size = max(1, min(batch_size, MAX_BATCH))
        self._flush_interval = flush_interval
        self._poll_interval = poll_interval
        self._now = now
        self.metrics = KeeperMetrics()
        self._blacklist: set[str] = set()
        self._attempts: dict[str, int] = {}
        self._first_seen: dict[str, float] = {}
        self._recover_probed = False
        self._task: asyncio.Task[None] | None = None

    # ---- 黑名单（拉黑联动最小实现：core 无挂起端点，P2 简化见 CONSTRAINTS §E） ----

    def blacklist(self, wallet: str) -> None:
        self._blacklist.add(wallet.lower())
        logger.warning("拉黑坏账消费者: %s", wallet)

    def blacklisted(self, wallet: str) -> bool:
        return wallet.lower() in self._blacklist

    # ---- 常驻任务 ----

    def start(self) -> None:
        if self.chain is None or self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="coincall-keeper")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        logger.info(
            "keeper 启动: batch_size=%d flush_interval=%.0fs",
            self._batch_size,
            self._flush_interval,
        )
        while True:
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.metrics.last_error = f"{type(exc).__name__}: {exc}"
                # exc_info：周期级异常偶发（如 10-07 23:46 / 10-08 11:02 的 TypeError），
                # 无栈回溯无法定位——全栈落日志是定位此类问题的唯一途径
                logger.warning(
                    "keeper 周期失败（行保持 pending，将重试）: %s",
                    self.metrics.last_error,
                    exc_info=exc,
                )
            await asyncio.sleep(self._poll_interval)

    # ---- 单个结算周期（确定性入口，单测直调） ----

    async def run_cycle(self) -> dict[str, Any]:
        pending = self.store.pending_settles(limit=MAX_BATCH)
        self.metrics.queue_depth_pending = len(pending)
        report: dict[str, Any] = {
            "flushed": False,
            "expired": 0,
            "recovered": 0,
            "unresolved": 0,
        }
        chain = self.chain
        if chain is None:
            return report

        if not self._recover_probed:
            self._recover_probed = True
            recovered, pending = await self._probe_recovered(chain, pending)
            report["recovered"] = recovered

        now = self._now()
        live: list[dict[str, Any]] = []
        for row in pending:
            if now >= int(_auth_of(row)["validBefore"]):
                self.store.mark_settle(row["call_id"], SettleStatus.EXPIRED)
                report["expired"] += 1
                logger.info("过期作废（等于从未扣款）: call=%s", row["call_id"])
            else:
                live.append(row)

        live_ids = {row["call_id"] for row in live}
        self._first_seen = {cid: ts for cid, ts in self._first_seen.items() if cid in live_ids}
        for row in live:
            self._first_seen.setdefault(row["call_id"], now)

        if live and len(live) < self._batch_size:
            oldest = min(self._first_seen[row["call_id"]] for row in live)
            if now - oldest < self._flush_interval:
                return report  # 未到笔数阈值也未到时间阈值：继续攒批

        calls: list[dict[str, Any]] = []
        submitted: list[dict[str, Any]] = []
        for row in live:
            wallet = await self._wallet_for(row) if self._wallet_for is not None else None
            if wallet is None:
                report["unresolved"] += 1
                logger.warning("provider 钱包未解析，本周期跳过: call=%s", row["call_id"])
                continue
            calls.append(self._build_call(row, str(wallet)))
            submitted.append(row)
        if not calls:
            return report

        report.update(await self._settle_batch(chain, calls, submitted))
        self.metrics.last_batch = dict(report)
        return report

    # ---- 内部 ---------------------------------------------------------------

    async def _probe_recovered(
        self, chain: SettleChain, pending: list[dict[str, Any]]
    ) -> tuple[int, list[dict[str, Any]]]:
        """重启首轮：nonce 已烧 = 上次已 Charged（合约只在成功后烧 nonce）。"""
        remaining: list[dict[str, Any]] = []
        recovered = 0
        for row in pending:
            auth = _auth_of(row)
            if not await chain.is_nonce_used(auth["nonce"]):
                remaining.append(row)
                continue
            self._mark_done(row["call_id"], int(auth["value"]))
            recovered += 1
            logger.info("宕机恢复补记 done: call=%s", row["call_id"])
        return recovered, remaining

    def _build_call(self, row: dict[str, Any], wallet: str) -> dict[str, Any]:
        auth = _auth_of(row)
        return {
            "provider": to_checksum_address(wallet),
            "auth": {
                "from": to_checksum_address(auth["from"]),
                "to": to_checksum_address(auth["to"]),
                "value": int(auth["value"]),
                "validAfter": int(auth["validAfter"]),
                "validBefore": int(auth["validBefore"]),
                "nonce": auth["nonce"],
            },
            "v": int(auth["v"]),
            "r": auth["r"],
            "s": auth["s"],
        }

    async def _settle_batch(
        self, chain: SettleChain, calls: list[dict[str, Any]], submitted: list[dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            tx_hash = await chain.submit_batch(calls)
            status = await self._wait_receipt(chain, tx_hash)
            if status != 1:
                raise SettleChainError(f"交易上链失败 status={status}: {tx_hash}")
            logs = await chain.receipt_logs(tx_hash)
        except SettleChainError as exc:
            self.metrics.last_error = str(exc)
            raise
        vault = str(calls[0]["auth"]["to"])
        events = decode_charge_events(logs, vault)
        outcome = self._apply_events(submitted, events, tx_hash)
        outcome["flushed"] = True
        return outcome

    async def _wait_receipt(self, chain: SettleChain, tx_hash: str) -> int:
        for _ in range(RECEIPT_POLL_ATTEMPTS):
            status = await chain.receipt_status(tx_hash)
            if status is not None:
                return status
            await asyncio.sleep(RECEIPT_POLL_INTERVAL_S)
        raise SettleChainError(f"回执轮询超时: {tx_hash}")

    def _apply_events(
        self, submitted: list[dict[str, Any]], events: list[ChargeEvent], tx_hash: str
    ) -> dict[str, Any]:
        by_nonce = {_auth_of(row)["nonce"].lower(): row for row in submitted}
        remaining = list(submitted)
        outcome: dict[str, Any] = {
            "tx_hash": tx_hash,
            "submitted": len(submitted),
            "charged": 0,
            "charged_raw": 0,
            "failed": 0,
            "expired": 0,
            "retried": 0,
        }
        for event in events:
            if event.kind == "charged":
                if event.value is None or event.nonce is None:
                    logger.warning("Charged 事件缺 value/nonce，跳过: %s", event)
                    continue
                row = by_nonce.pop(event.nonce.lower(), None)
                if row is None:
                    logger.warning("无法归属的 Charged 事件: nonce=%s", event.nonce)
                    continue
                remaining.remove(row)
                self._mark_done(row["call_id"], event.value)
                outcome["charged"] += 1
                outcome["charged_raw"] += event.value
                continue
            row = next(
                (r for r in remaining if _auth_of(r)["from"].lower() == event.from_.lower()),
                None,
            )
            if row is None:
                logger.warning(
                    "无法归属的 ChargeFailed 事件: from=%s reason=%s", event.from_, event.reason
                )
                continue
            remaining.remove(row)
            by_nonce.pop(_auth_of(row)["nonce"].lower(), None)
            self._on_charge_failed(row, str(event.reason), outcome)
        if remaining:
            logger.warning(
                "回执缺 %d 笔事件，保持 pending 待下轮（nonce 未烧安全）", len(remaining)
            )
        return outcome

    def _on_charge_failed(self, row: dict[str, Any], reason: str, outcome: dict[str, Any]) -> None:
        auth = _auth_of(row)
        if reason == REASON_TRANSFER_FAILED:
            attempts = self._attempts.get(row["call_id"], 0) + 1
            self._attempts[row["call_id"]] = attempts
            if attempts < MAX_TRANSFER_ATTEMPTS:
                outcome["retried"] += 1
                logger.info(
                    "transfer_failed：nonce 未烧，携原签名重试 (%d/%d): call=%s",
                    attempts,
                    MAX_TRANSFER_ATTEMPTS,
                    row["call_id"],
                )
                return
            self._mark_bad_debt(row["call_id"], auth["from"])
            outcome["failed"] += 1
        elif reason in EXPIRE_REASONS:
            self.store.mark_settle(row["call_id"], SettleStatus.EXPIRED)
            outcome["expired"] += 1
            logger.info("链上判过期（%s，等于从未扣款）: call=%s", reason, row["call_id"])
        elif reason in ALARM_REASONS:
            self.store.mark_settle(row["call_id"], SettleStatus.FAILED)
            outcome["failed"] += 1
            logger.error(
                "keeper 侧出现 %s（网关验签已拦，出现即 bug）: call=%s", reason, row["call_id"]
            )
        else:
            self.store.mark_settle(row["call_id"], SettleStatus.FAILED)
            outcome["failed"] += 1
            logger.warning("未知 reason=%s: call=%s", reason, row["call_id"])

    def _mark_done(self, call_id: str, value_raw: int) -> None:
        self.store.mark_settle(call_id, SettleStatus.DONE)
        self.store.mark_call(call_id, CallStatus.SETTLED)
        self.metrics.cumulative_charged_raw += value_raw
        self.metrics.cumulative_charged_count += 1

    def _mark_bad_debt(self, call_id: str, consumer: str) -> None:
        self.store.mark_settle(call_id, SettleStatus.FAILED)
        self.store.mark_call(call_id, CallStatus.BAD_DEBT)
        self.blacklist(consumer)
        logger.warning(
            "坏账落表（settle=failed + calls=bad_debt）: call=%s consumer=%s", call_id, consumer
        )

    # ---- 审计 F-01 对账（按需触发，与结算主循环零耦合；纯只读） ----------------

    async def reconcile(
        self,
        *,
        window_hours: float = 24.0,
        from_block: int | None = None,
    ) -> dict[str, Any]:
        """Charged 链上事件 ↔ settle_queue 近窗行逐笔对账（审计 F-01 缓解）。

        纯只读：不改 settle_queue 状态、不发交易、不触碰结算循环。两侧窗口共用同一
        cutoff：缺省 now-window_hours（链侧起点经块时间戳二分定位 from_block）；
        from_block 显式覆写时 cutoff 钉到该块时间戳（部署块起点全量对账用）。
        不平（done 无 Charged / 链上有队列无 / 四元组错位）即 WARNING 逐笔告警。
        """
        chain = self.chain
        if chain is None:
            raise SettleChainError(
                "keeper 未装配链上通道（COINCALL_KEEPER_ENABLED=false），对账不可用"
            )
        window_s = window_hours * 3600.0
        cutoff_ts = self._now() - window_s
        tip = await chain.latest_block()
        if from_block is not None:
            explicit = True
            start_block = max(0, from_block)
            cutoff_ts = float(await chain.block_timestamp(start_block))
        else:
            explicit = False
            start_block = await self._block_at_or_after(chain, cutoff_ts, tip, window_s)
        events = await chain.charged_events(start_block, tip)
        # 队列侧窗口与 cutoff 对齐：from_block 覆写时换算回等效小时数（SQL 时钟域内过滤）
        rows = self.store.settle_rows_since(hours=max(0.0, (self._now() - cutoff_ts) / 3600.0))
        wallets: dict[str, str | None] = {}
        for row in rows:
            if self._wallet_for is None:
                break
            wallet = await self._wallet_for(row)
            wallets[str(row["call_id"])] = str(wallet) if wallet is not None else None
        report = reconcile_charged(rows, wallets, events)
        report["mode"] = str(getattr(chain, "mode", "direct"))
        report["window"] = {
            "window_hours": window_hours,
            "cutoff_ts": cutoff_ts,
            "from_block": start_block,
            "to_block": tip,
            "from_block_explicit": explicit,
        }
        self.metrics.last_reconcile = {
            "at": self._now(),
            "ok": report["ok"],
            "matched": report["matched"],
            "queue_rows": report["queue_rows"],
            "chain_events": report["chain_events"],
            "queue_only": len(report["queue_only"]),
            "chain_only": len(report["chain_only"]),
            "field_mismatch": len(report["field_mismatch"]),
            "window": dict(report["window"]),
        }
        if report["ok"]:
            logger.info(
                "F-01 对账通过: matched=%d/%d queue_rows=%d blocks=[%d,%d]",
                report["matched"],
                report["chain_events"],
                report["queue_rows"],
                start_block,
                tip,
            )
        else:
            logger.warning(
                "F-01 对账不平（Charged ↔ settle_queue）: %s",
                json.dumps(
                    {
                        "queue_only": report["queue_only"],
                        "chain_only": report["chain_only"],
                        "field_mismatch": report["field_mismatch"],
                        "window": report["window"],
                    },
                    ensure_ascii=False,
                ),
            )
        return report

    async def _block_at_or_after(
        self, chain: SettleChain, cutoff_ts: float, tip: int, window_s: float
    ) -> int:
        """二分找第一个 timestamp ≥ cutoff 的块号（块时间戳单调）。

        搜索范围钳在 [tip - window_s/MIN_BLOCK_TIME_S, tip]：真实链 interval ≥
        MIN_BLOCK_TIME_S 时覆盖完整窗口，又不触达远古块头。
        """
        span = int(window_s / MIN_BLOCK_TIME_S) + 1
        lo = max(0, tip - span)
        hi = tip
        for _ in range(BLOCK_PROBE_LIMIT):
            if lo >= hi:
                break
            mid = (lo + hi) // 2
            if float(await chain.block_timestamp(mid)) < cutoff_ts:
                lo = mid + 1
            else:
                hi = mid
        return lo

    # ---- 可观测 ----

    def status_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.chain is not None,
            "running": self._task is not None and not self._task.done(),
            "batch_size": self._batch_size,
            "flush_interval_s": self._flush_interval,
            "queue": settle_counts(self.store),
            "last_batch": self.metrics.last_batch,
            "last_reconcile": self.metrics.last_reconcile,
            "cumulative_charged_count": self.metrics.cumulative_charged_count,
            "cumulative_charged_raw": str(self.metrics.cumulative_charged_raw),
            "blacklisted_consumers": sorted(self._blacklist),
            "last_error": self.metrics.last_error,
        }


#: settle 行 → provider agentWallet（记账键=agentWallet，04 §2）
WalletResolver = Callable[[dict[str, Any]], Any]


def provider_wallet_resolver(
    overrides: dict[str, str],
    store: CallStore,
    manifests: Any,
) -> WalletResolver:
    """默认解析链：静态覆盖（agent_id → wallet）→ calls.service_id → core manifest。"""

    async def wallet_for(row: dict[str, Any]) -> str | None:
        hit = overrides.get(str(row["provider_token_id"]))
        if hit:
            return hit
        from app.modules.manifest_client import (  # noqa: PLC0415 —— 避免装配期 import 环
            ServiceNotFoundError,
        )

        call = store.get_call(str(row["call_id"]))
        if call is None:
            return None
        try:
            info = await manifests.get(str(call["service_id"]))
        except ServiceNotFoundError:
            return None
        return info.manifest.provider.wallet

    return wallet_for
