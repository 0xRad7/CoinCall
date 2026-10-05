"""内置 demo 服务（07 §2 / 09 P1-4）：internal:// 端点的网关本地执行。

分发规则：manifest endpoint.type=internal 且 url 形如 ``internal://<name>`` 时按
name 路由到对应 handler；无 url / 非 internal 前缀 / 未注册名 → 回显兜底
（InternalEchoProvider 语义保持）——避免 demo 网络单点。

三个 handler（09 P1-4）：
- translate       回显式翻译兜底（友队 http_json 掉线时 manifest 热切换目标）
- chain_report    8010 /api/v1/chain/health + core /stats/overview → 链报告文本
- contract_scan   8010 /api/v1/chain/info + /api/v1/contracts/logs → PayVault Charged 摘要

handler 出站（8010/8020，均本机）失败 → InternalServiceError（ProviderError 子类）→
calls=aborted 零扣款（A7 同语义：Provider 失败不收钱）。
挂在既有 ProviderAdapter 抽象下；不触碰冻结契约文件（payment.py/schemes.py/calls.py）。
"""

import time
from collections.abc import Mapping
from typing import Any, Protocol

import httpx
from eth_utils import keccak

from app.modules.manifest_client import ManifestInfo
from app.modules.providers import InternalEchoProvider, ProviderError, ProviderResult

INTERNAL_URL_PREFIX = "internal://"
TRANSLATE_NAME = "translate"
CHAIN_REPORT_NAME = "chain_report"
CONTRACT_SCAN_NAME = "contract_scan"

#: PayVault 事件签名静态锚（coincall-contracts/contracts/PayVault.sol）
CHARGED_EVENT_SIG = "Charged(address,address,uint256,bytes32)"
CHARGED_TOPIC0 = "0x" + keccak(text=CHARGED_EVENT_SIG).hex()
SCAN_WINDOW_DEFAULT = 300
SCAN_WINDOW_CAP = 5000  # 8010 /contracts/logs 的 getLogs 窗口硬顶
TOKEN_DECIMALS = 6
HTTP_2XX_BASE = 2


def handler_name(url: str | None) -> str | None:
    """internal://<name> → name；其余（含无 url）→ None（回显兜底）。"""
    if not url or not url.startswith(INTERNAL_URL_PREFIX):
        return None
    name = url[len(INTERNAL_URL_PREFIX) :].strip("/")
    return name or None


def fmt_amount(raw: int, decimals: int = TOKEN_DECIMALS) -> str:
    """最小单位 → 定点字符串（演示口径，不做银行家舍入）。"""
    whole, frac = divmod(max(raw, 0), 10**decimals)
    return f"{whole}.{frac:0{decimals}d}"


class InternalServiceError(ProviderError):
    """internal 依赖（8010/8020）不可达或非 2xx——走 A7 aborted 零扣款路径。"""


async def _get_json(http: httpx.AsyncClient, url: str, timeout_s: float) -> dict[str, Any]:
    """GET → dict；任何故障统一 InternalServiceError（fail-closed，不外联重试）。"""
    try:
        resp = await http.get(url, timeout=timeout_s)
    except httpx.HTTPError as exc:
        raise InternalServiceError(f"internal 依赖不可达: {url} ({exc})") from exc
    if resp.status_code // 100 != HTTP_2XX_BASE:
        raise InternalServiceError(f"internal 依赖非 2xx: {url} → {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise InternalServiceError(f"internal 依赖响应非 JSON: {url}") from exc
    if not isinstance(body, dict):
        raise InternalServiceError(f"internal 依赖响应非对象: {url}")
    return body


class InternalHandler(Protocol):
    """internal:// handler 契约（挂 ProviderAdapter 分发之下）。"""

    name: str

    async def handle(self, manifest: ManifestInfo, body: Any) -> ProviderResult: ...


class TranslateHandler:
    """svc_translate 的 internal 兜底：确定性回显"翻译"（07 §2 兜底策略）。"""

    name = TRANSLATE_NAME
    ENGINE = "internal-fallback"
    MARK = "[coincall 内置翻译兜底]"

    async def handle(self, manifest: ManifestInfo, body: Any) -> ProviderResult:
        text = body.get("text") if isinstance(body, dict) else None
        translated = f"{self.MARK} {text}" if isinstance(text, str) and text else text
        return ProviderResult(
            status_code=200,
            body={
                "service_id": manifest.service_id,
                "provider_agent_id": manifest.manifest.provider.agent_id,
                "text": text,
                "translated": translated,
                "engine": self.ENGINE,
            },
        )


class ChainReportHandler:
    """svc_chain_report：8010 链通道健康 + core 平台总览 → 链报告（文本+机读）。"""

    name = CHAIN_REPORT_NAME

    def __init__(
        self,
        http: httpx.AsyncClient,
        bot_chain_base_url: str,
        core_base_url: str,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        self.http = http
        self.bot_chain = bot_chain_base_url.rstrip("/")
        self.core = core_base_url.rstrip("/")
        self.timeout_s = timeout_s

    async def handle(self, manifest: ManifestInfo, body: Any) -> ProviderResult:
        health = await _get_json(self.http, f"{self.bot_chain}/api/v1/chain/health", self.timeout_s)
        overview = await _get_json(self.http, f"{self.core}/stats/overview", self.timeout_s)
        report = self._render(health, overview)
        return ProviderResult(
            status_code=200,
            body={
                "service_id": manifest.service_id,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "report": report,
                "chain_health": health,
                "platform_overview": overview,
            },
        )

    def _render(self, health: dict[str, Any], overview: dict[str, Any]) -> str:
        lines = ["CoinCall 链报告（BOT Chain 测试网 968）"]
        channels = health.get("channels")
        if isinstance(channels, dict):
            overall = "ok" if health.get("ok") else "DEGRADED"
            lines.append(f"链通道健康: {overall}")
            for name, ch in channels.items():
                if isinstance(ch, dict):
                    ok = "ok" if ch.get("ok") else "DOWN"
                    lines.append(f"  {name}: {ok} ({ch.get('latency_ms')}ms)")
        lines.append(
            f"平台总调用 {overview.get('calls_success_total')}（成功"
            f" {overview.get('calls_success_total')} / 中止 {overview.get('calls_aborted_total')}）"
        )
        lines.append(
            f"链上 GMV {overview.get('gmv')} USDT（Charged {overview.get('charged_count')} 笔）"
        )
        lines.append(
            f"服务 {overview.get('services_active')}/{overview.get('services_total')} active，"
            f"Providers {overview.get('providers_registered')}"
            f"（有收入 {overview.get('providers_with_revenue')}）"
        )
        lines.append(f"同步至块: {overview.get('synced_to_block')}")
        return "\n".join(lines)


def _addr_from_topic(topic: str) -> str:
    return "0x" + topic.removeprefix("0x")[-40:]


#: Charged 事件 topics 下标（topic0=签名 / 1=provider / 2=from / 3=nonce）
_TOPIC_PROVIDER, _TOPIC_FROM, _TOPIC_NONCE = 1, 2, 3


def decode_charged_log(log: dict[str, Any]) -> dict[str, Any]:
    """8010 /contracts/logs 的原始 log → Charged 事件行（ABI 手工解码：value=uint256 data）。"""
    topics = [str(t) for t in log.get("topics") or []]
    data = str(log.get("data") or "0x")
    value_raw = int(data, 16) if data not in ("", "0x") else 0

    def addr(idx: int) -> str | None:
        return _addr_from_topic(topics[idx]) if len(topics) > idx else None

    return {
        "tx_hash": log.get("transaction_hash"),
        "block_number": log.get("block_number"),
        "log_index": log.get("log_index"),
        "provider": addr(_TOPIC_PROVIDER),
        "from": addr(_TOPIC_FROM),
        "value_raw": value_raw,
        "value": fmt_amount(value_raw),
        "nonce": topics[_TOPIC_NONCE] if len(topics) > _TOPIC_NONCE else None,
    }


class ContractScanHandler:
    """svc_contract_scan：经 8010 扫 PayVault 最近 Charged 事件 → 结构化摘要。"""

    name = CONTRACT_SCAN_NAME

    def __init__(
        self,
        http: httpx.AsyncClient,
        bot_chain_base_url: str,
        pay_vault: str,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        self.http = http
        self.bot_chain = bot_chain_base_url.rstrip("/")
        self.pay_vault = pay_vault
        self.timeout_s = timeout_s

    async def handle(self, manifest: ManifestInfo, body: Any) -> ProviderResult:
        window = self._window(body)
        info = await _get_json(self.http, f"{self.bot_chain}/api/v1/chain/info", self.timeout_s)
        try:
            tip = int(info["block_number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InternalServiceError(f"8010 /chain/info 缺 block_number: {info!r}") from exc
        from_block = max(0, tip - window)
        logs = await _get_json(
            self.http,
            f"{self.bot_chain}/api/v1/contracts/logs"
            f"?address={self.pay_vault}&topic0={CHARGED_TOPIC0}"
            f"&from_block={from_block}&to_block={tip}",
            self.timeout_s,
        )
        events = [decode_charged_log(log) for log in logs.get("logs") or []]
        total = sum(int(e["value_raw"] or 0) for e in events)
        return ProviderResult(
            status_code=200,
            body={
                "service_id": manifest.service_id,
                "pay_vault": self.pay_vault,
                "event": "Charged",
                "window": {
                    "from_block": from_block,
                    "to_block": tip,
                    "blocks": tip - from_block + 1,
                },
                "count": len(events),
                "total_value_raw": total,
                "total_value": fmt_amount(total),
                "events": events,
            },
        )

    def _window(self, body: Any) -> int:
        req = body if isinstance(body, dict) else {}
        try:
            window = int(req.get("window_blocks") or SCAN_WINDOW_DEFAULT)
        except (TypeError, ValueError):
            return SCAN_WINDOW_DEFAULT
        if window <= 0:
            return SCAN_WINDOW_DEFAULT
        return min(window, SCAN_WINDOW_CAP)


class InternalServicesProvider:
    """internal 型端点分发器（ProviderAdapter 实现）。

    internal://<name> → 注册的 handler；无 url / 未知名 / 非前缀 → 回显兜底。
    handlers 显式注入优先（单测替身入口）；缺省按装配参数构建三个 demo handler。
    """

    name = "internal-services"

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        bot_chain_base_url: str = "",
        core_base_url: str = "",
        pay_vault: str = "",
        handlers: Mapping[str, InternalHandler] | None = None,
    ) -> None:
        if handlers is not None:
            self._handlers: dict[str, InternalHandler] = dict(handlers)
        else:
            if http is None:
                raise ValueError("InternalServicesProvider 缺省装配需要 httpx.AsyncClient")
            self._handlers = {
                TranslateHandler.name: TranslateHandler(),
                ChainReportHandler.name: ChainReportHandler(
                    http, bot_chain_base_url, core_base_url
                ),
                ContractScanHandler.name: ContractScanHandler(http, bot_chain_base_url, pay_vault),
            }
        self._echo = InternalEchoProvider()

    async def forward(self, manifest: ManifestInfo, body: Any) -> ProviderResult:
        name = handler_name(manifest.manifest.endpoint.url)
        handler = self._handlers.get(name) if name is not None else None
        if handler is None:
            return await self._echo.forward(manifest, body)
        return await handler.handle(manifest, body)
