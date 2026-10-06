"""决策摘要锚链任务（10 §1/§2 + G2 冻结契约）：keeper 循环旁挂的常驻协程。

链路（每 ``COINCALL_ANCHOR_INTERVAL_S``，默认 1800s，只在 keeper enabled 时启动）：
  ① ``GET {core}/internal/decision/anchor-pending`` —— core 聚合好的待锚摘要列表；
  ② 对每条经 bot-chain-api ``POST /api/v1/contracts/send`` 调 ERC-8004
     ``setMetadata(tokenId, key, bytes)``（to=IdentityRegistry，from=出资账户/operator，
     key="coincall:decision:v1"，value=digest|pointer 的 UTF-8 bytes，tokenId=provider
     的 agent_id，ABI 副本见 app/core/abis/erc8004.py）；
  ③ 成功回 ``POST {core}/internal/decision/anchor-result``（anchor_id+tx_hash 指针）；
  ④ 失败记日志下轮重试——幂等靠 core 的 anchor_records（缺回执即重发，setMetadata
     同值覆盖天然幂等）。

与 core 的 JSON 契约（G2 冻结，并行 core 任务对齐）：
  GET  /internal/decision/anchor-pending
       → {"pending": [{"anchor_id": str, "token_id": int, "digest": str,
                       "pointer": str|null, "key": str?}]}
  POST /internal/decision/anchor-result
       ← {"anchor_id": str, "tx_hash": str, "token_id": int, "key": str, "value": str}

降级语义：core（8020）不可达只告警不阻塞（结算主循环与锚定是独立协程，互不等待）。
gas 由出资账户承担（README 注明）；写经 bot-chain-api，本仓不持私钥（铁律 P1）。
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.abis.erc8004 import set_metadata_abi

logger = logging.getLogger("coincall.gateway.anchor")

#: 锚定元数据键（10 §1/§2 冻结契约）
ANCHOR_KEY = "coincall:decision:v1"
#: 默认锚定周期（秒）
DEFAULT_ANCHOR_INTERVAL_S = 1800.0
HTTP_TIMEOUT_S = 60.0  # /contracts/send 服务端等回执 30s → 客户端放宽（keeper 同口径）
HTTP_OK = 200


class AnchorChainError(Exception):
    """锚定提交失败（条目保持待锚定，下轮重试）。"""


class AnchorCoreError(Exception):
    """core 依赖不可达（本周期降级为告警，不阻塞）。"""


@dataclass(frozen=True)
class AnchorPending:
    """一条待锚摘要（core anchor-pending 行；value=digest|pointer UTF-8 bytes）。"""

    anchor_id: str
    token_id: int
    digest: str
    pointer: str | None = None
    key: str = ANCHOR_KEY

    def value(self) -> str:
        return f"{self.digest}|{self.pointer}" if self.pointer else self.digest

    @classmethod
    def from_json(cls, row: dict[str, Any]) -> "AnchorPending":
        return cls(
            anchor_id=str(row["anchor_id"]),
            token_id=int(row["token_id"]),
            digest=str(row["digest"]),
            pointer=row.get("pointer"),
            key=str(row.get("key") or ANCHOR_KEY),
        )


class DecisionCoreClient:
    """core 决策面客户端（anchor-pending 拉取 / anchor-result 回执上报）。"""

    def __init__(self, *, base_url: str, http: httpx.AsyncClient) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http

    async def anchor_pending(self) -> list[AnchorPending]:
        try:
            resp = await self._http.get(f"{self.base_url}/internal/decision/anchor-pending")
        except httpx.HTTPError as exc:
            raise AnchorCoreError(f"anchor-pending 拉取失败: {exc}") from exc
        if resp.status_code != HTTP_OK:
            raise AnchorCoreError(f"anchor-pending 被拒({resp.status_code}): {resp.text[:200]}")
        rows = resp.json().get("pending") or []
        return [AnchorPending.from_json(row) for row in rows if isinstance(row, dict)]

    async def anchor_result(
        self, *, anchor_id: str, tx_hash: str, token_id: int, key: str, value: str
    ) -> None:
        payload = {
            "anchor_id": anchor_id,
            "tx_hash": tx_hash,
            "token_id": token_id,
            "key": key,
            "value": value,
        }
        try:
            resp = await self._http.post(
                f"{self.base_url}/internal/decision/anchor-result", json=payload
            )
        except httpx.HTTPError as exc:
            raise AnchorChainError(f"anchor-result 上报失败: {exc}") from exc
        if resp.status_code != HTTP_OK:
            raise AnchorChainError(f"anchor-result 被拒({resp.status_code}): {resp.text[:200]}")


class BotChainAnchorChain:
    """锚定链上通道：IdentityRegistry.setMetadata 经 bot-chain-api 提交（from=出资账户）。"""

    def __init__(
        self,
        *,
        base_url: str,
        registry_address: str,
        operator_address: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.registry_address = registry_address
        self.operator_address = operator_address
        # trust_env=False：本机服务直连，C-07——系统代理会把 127.0.0.1 劫持成 502 空体
        self._http = http or httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, trust_env=False)
        self._owns_http = http is None

    async def submit_anchor(self, token_id: int, key: str, value: str) -> str:
        """setMetadata(tokenId, key, UTF-8(value)) → tx_hash（失败抛 AnchorChainError）。"""
        payload = {
            "from_address": self.operator_address,
            "to": self.registry_address,
            "abi": set_metadata_abi(),
            "method": "setMetadata",
            "args": [token_id, key, "0x" + value.encode().hex()],
            "value_wei": "0",
            "dry_run": False,
        }
        try:
            resp = await self._http.post(f"{self.base_url}/api/v1/contracts/send", json=payload)
        except httpx.HTTPError as exc:
            raise AnchorChainError(f"锚定提交失败: {exc}") from exc
        if resp.status_code != HTTP_OK:
            raise AnchorChainError(f"setMetadata 被拒({resp.status_code}): {resp.text[:300]}")
        body = resp.json()
        tx_hash = body.get("tx_hash")
        if not tx_hash:
            raise AnchorChainError(f"锚定响应缺 tx_hash: {body}")
        if int(body.get("status", 0)) != 1:
            raise AnchorChainError(f"锚定交易上链失败 status={body.get('status')}: {tx_hash}")
        return str(tx_hash)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()


@dataclass
class AnchorMetrics:
    """可观测累计量（/internal/keeper/status 的 anchor 段直读）。"""

    anchored_count: int = 0
    last_run_at: float | None = None
    last_error: str | None = None
    last_report: dict[str, Any] = field(default_factory=dict)


class AnchorTask:
    """锚定常驻协程：周期拉 pending → 逐条提交 → 回执上报；任何失败只告警不阻塞。"""

    def __init__(
        self,
        *,
        core: DecisionCoreClient,
        chain: BotChainAnchorChain,
        interval_s: float = DEFAULT_ANCHOR_INTERVAL_S,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.core = core
        self.chain = chain
        self.interval_s = interval_s
        self.metrics = AnchorMetrics()
        self._now = now
        self._task: asyncio.Task[None] | None = None

    # ---- 常驻任务 ----

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="coincall-anchor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        logger.info("anchor 启动: interval=%.0fs key=%s", self.interval_s, ANCHOR_KEY)
        while True:
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 常驻循环兜底：只告警不退出
                self.metrics.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("anchor 周期异常（不阻塞结算主循环）: %s", self.metrics.last_error)
            await asyncio.sleep(self.interval_s)

    # ---- 单周期（确定性入口，单测直调） ----

    async def run_cycle(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "pending": 0,
            "submitted": 0,
            "reported": 0,
            "failed": 0,
            "report_failed": 0,
            "degraded": False,
        }
        self.metrics.last_run_at = self._now()
        try:
            pending = await self.core.anchor_pending()
        except AnchorCoreError as exc:
            # 8020 依赖失败：降级为告警（结算主循环是独立协程，不受影响）
            report["degraded"] = True
            self.metrics.last_error = str(exc)
            logger.warning("anchor 降级（core 不可达，本轮跳过）: %s", exc)
            self.metrics.last_report = dict(report)
            return report

        report["pending"] = len(pending)
        for item in pending:
            value = item.value()
            try:
                tx_hash = await self.chain.submit_anchor(item.token_id, item.key, value)
            except AnchorChainError as exc:
                report["failed"] += 1
                self.metrics.last_error = str(exc)
                logger.warning(
                    "锚定提交失败（下轮重试，幂等靠 core anchor_records）: "
                    "anchor=%s token=%s err=%s",
                    item.anchor_id,
                    item.token_id,
                    exc,
                )
                continue
            report["submitted"] += 1
            try:
                await self.core.anchor_result(
                    anchor_id=item.anchor_id,
                    tx_hash=tx_hash,
                    token_id=item.token_id,
                    key=item.key,
                    value=value,
                )
                report["reported"] += 1
                self.metrics.anchored_count += 1
                logger.info(
                    "锚定完成: anchor=%s token=%d tx=%s", item.anchor_id, item.token_id, tx_hash
                )
            except AnchorChainError as exc:
                # 已上链但 core 未记账：core 缺回执会重发 → setMetadata 同值覆盖幂等
                report["report_failed"] += 1
                self.metrics.last_error = str(exc)
                logger.warning(
                    "锚定回执上报失败（core 将重发，setMetadata 幂等覆盖）: anchor=%s err=%s",
                    item.anchor_id,
                    exc,
                )
        self.metrics.last_report = dict(report)
        return report

    # ---- 可观测 ----

    def status_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "running": self._task is not None and not self._task.done(),
            "interval_s": self.interval_s,
            "key": ANCHOR_KEY,
            "anchored_count": self.metrics.anchored_count,
            "last_run_at": (
                int(self.metrics.last_run_at) if self.metrics.last_run_at is not None else None
            ),
            "last_report": self.metrics.last_report or None,
            "last_error": self.metrics.last_error,
        }
