"""排行榜与统计（06 篇）：收入=链上 Charged 唯一真相 + 活跃度=网关 calls 双源。

数据面设计（P1-3，v2 无 topups）：
- **收入/GMV**：以 PayVault `Charged(address indexed provider, address indexed from,
  uint256 value, bytes32 indexed nonce)` 事件为唯一真相——经 8010 的
  `GET /contracts/logs` 透传端点增量拉取（首跑自部署块回补、分窗 ≤5000 块），
  落 charged_events 库存表 + sync_watermarks 水位表（幂等可重放）；
- **活跃度**：经网关 `GET /internal/stats/calls`（success/aborted/pending/坏账）；
- 排序口径固定**收入优先**（00 铁律：收入=最硬信誉，无评价机制）；
- 懒同步：排行榜端点请求时增量同步（进程内锁 + 最小间隔防打爆）；同步失败不 500，
  按库存返回并附 sync 说明（链不可达且库存为空 → 优雅空列表，06 §5-2）。
"""

import time
from decimal import Decimal
from typing import Any, Protocol

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.errors import ApiError
from app.modules.manifest import manifest_category, manifest_tags
from app.storage.db import CoreStore

router = APIRouter(tags=["leaderboard"])

#: Charged(address,address,uint256,bytes32) 的 keccak256（eth_utils 离线计算定档；
#: 与链上实测事件 topic 逐一核对过，改动=合约重部署级别变更）
CHARGED_TOPIC0 = "0x7cbb811de7ebfc8f2d6195f3af05b0b12fc3e9c8b48a2dbf3ff1c4ef81291dbf"

#: USDT 6 位精度（与 manifest TOKEN_DECIMALS 同源口径）
TOKEN_DECIMALS = 6

#: Charged proof 交易链接基址（缺省=测试网；运行时以 settings.explorer_tx_base 为准，主网 env 覆盖）
EXPLORER_TX_BASE = "https://scan.bohr.life/tx/"

STREAM_CHARGED = "charged_events"

#: Charged 事件 topics 数（签名+provider+from+nonce）
CHARGED_TOPIC_COUNT = 4
#: EVM 地址 0x + 40 hex
WALLET_HEX_LEN = 42


def to_amount(value_raw: int) -> str:
    """最小单位 → 人类可读十进制字符串（仅展示；权威值恒为 *_raw）。

    6 位精度缩放后去掉尾零：10000000→"10"、70000→"0.07"、0→"0"。
    """
    scaled = format(Decimal(value_raw).scaleb(-TOKEN_DECIMALS), "f")
    if "." in scaled:
        scaled = scaled.rstrip("0").rstrip(".")
    return scaled or "0"


# ---------------------------------------------------------------------------
# 数据源协议与真实实现
# ---------------------------------------------------------------------------


class ChainSource(Protocol):
    """链源协议：8010 的只读视图（tip + Charged 日志）。"""

    def tip_block(self) -> int: ...

    def charged_logs(self, from_block: int, to_block: int) -> list[dict[str, Any]]: ...


class BotChainClient:
    """真实链源：8010 GET /chain/info（链尖）+ GET /contracts/logs（Charged 透传）。"""

    def __init__(self, http: httpx.Client, base_url: str, pay_vault: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._pay_vault = pay_vault

    def tip_block(self) -> int:
        resp = self._http.get(f"{self._base}/api/v1/chain/info")
        resp.raise_for_status()
        return int(resp.json()["block_number"])

    def charged_logs(self, from_block: int, to_block: int) -> list[dict[str, Any]]:
        resp = self._http.get(
            f"{self._base}/api/v1/contracts/logs",
            params={
                "address": self._pay_vault,
                "from_block": from_block,
                "to_block": to_block,
                "topic0": CHARGED_TOPIC0,
                "limit": 5000,
            },
        )
        resp.raise_for_status()
        return list(resp.json().get("logs", []))


class GatewayStatsSource(Protocol):
    """活跃度源协议：网关聚合视图（window_hours=None 为网关默认全量口径）。"""

    def stats_view(self, window_hours: int | None = None) -> dict[str, Any]: ...


class GatewayStatsClient:
    """真实活跃度源：8030 GET /internal/stats/calls（决策层带 ?window_hours= 窗口口径）。"""

    def __init__(self, http: httpx.Client, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    def stats_view(self, window_hours: int | None = None) -> dict[str, Any]:
        params = {} if window_hours is None else {"window_hours": window_hours}
        resp = self._http.get(f"{self._base}/internal/stats/calls", params=params)
        resp.raise_for_status()
        return dict(resp.json())


# ---------------------------------------------------------------------------
# Charged 增量索引（库存 + 水位）
# ---------------------------------------------------------------------------


def decode_charged(log: dict[str, Any]) -> dict[str, Any] | None:
    """原始 Charged log → 事件行；形态不符返回 None（防御，不毒死整批）。"""
    topics = log.get("topics") or []
    if len(topics) < CHARGED_TOPIC_COUNT or topics[0] != CHARGED_TOPIC0:
        return None
    try:
        return {
            "tx_hash": str(log["transaction_hash"]),
            "log_index": int(log.get("log_index", 0)),
            "block_number": int(log["block_number"]),
            "provider": "0x" + topics[1][-40:].lower(),
            "payer": "0x" + topics[2][-40:].lower(),
            "value_raw": int(str(log["data"]), 16),
            "nonce": str(topics[3]),
        }
    except (KeyError, ValueError):
        return None


class ChargedIndexer:
    """增量拉取器：水位（首次=部署块-安全余量）→ 链尖，分窗 ≤window 调用链源。"""

    def __init__(
        self,
        store: CoreStore,
        chain: ChainSource,
        deploy_block: int,
        window: int = 5000,
        safety: int = 64,
    ) -> None:
        self._store = store
        self._chain = chain
        self._deploy_block = deploy_block
        self._window = window
        self._safety = safety

    def sync(self) -> dict[str, int]:
        tip = self._chain.tip_block()
        watermark = self._store.get_watermark(STREAM_CHARGED)
        start = max(0, self._deploy_block - self._safety) if watermark is None else watermark + 1
        pulled = 0
        inserted = 0
        if start > tip:
            return {
                "from_block": start,
                "to_block": tip,
                "pulled": 0,
                "inserted": 0,
                "synced_to": self._store.get_watermark(STREAM_CHARGED) or tip,
            }
        cursor = start
        while cursor <= tip:
            window_to = min(cursor + self._window - 1, tip)
            logs = self._chain.charged_logs(cursor, window_to)
            events = [ev for ev in (decode_charged(log) for log in logs) if ev is not None]
            pulled += len(logs)
            inserted += self._store.insert_charged_events(events)
            self._store.set_watermark(STREAM_CHARGED, window_to)
            cursor = window_to + 1
        return {
            "from_block": start,
            "to_block": tip,
            "pulled": pulled,
            "inserted": inserted,
            "synced_to": tip,
        }


# ---------------------------------------------------------------------------
# 视图模型
# ---------------------------------------------------------------------------


class ProviderRevenueRow(BaseModel):
    wallet: str
    display_name: str | None = None
    agent_id: int | None = None
    revenue_raw: int
    revenue: str = Field(description="展示值（USDT 十进制）；权威值为 revenue_raw")
    charged_count: int


class LeaderboardProvidersResponse(BaseModel):
    order: str = "revenue"
    providers: list[ProviderRevenueRow]


class ServiceStatsRow(BaseModel):
    service_id: str
    name: str
    status: str
    category: str = "other"
    tags: list[str] = Field(default_factory=list)
    provider_wallet: str
    provider_agent_id: int | None = None
    display_name: str | None = None
    revenue_raw: int
    revenue: str
    charged_count: int
    calls_success: int | None = None
    calls_aborted: int | None = None
    fail_rate: float | None = Field(default=None, description="aborted/(success+aborted)")
    last_call_at: str | None = None


class LeaderboardServicesResponse(BaseModel):
    order: str = "revenue"
    services: list[ServiceStatsRow]


class StoreResponse(BaseModel):
    """目录 × 收入聚合（06 §3 /store：active 服务的人读商店页后端）。"""

    order: str = "revenue"
    services: list[ServiceStatsRow]


class OverviewResponse(BaseModel):
    gmv_raw: int = Field(description="链上 Charged 总额（唯一真相，06 §5-3）")
    gmv: str
    charged_count: int
    calls_success_total: int | None = None
    calls_aborted_total: int | None = None
    services_total: int
    services_active: int
    providers_registered: int
    providers_with_revenue: int
    synced_to_block: int | None = Field(description="Charged 库存水位（链尖对齐度）")
    sources: dict[str, str] = Field(
        default_factory=lambda: {
            "revenue": "onchain Charged(PayVault) via bot-chain-api /contracts/logs",
            "activity": "gateway /internal/stats/calls",
        }
    )
    degraded: list[str] = Field(default_factory=list, description="降级说明（同步/活跃度源失败）")


class ProofEvent(BaseModel):
    tx_hash: str
    explorer_url: str
    log_index: int
    block_number: int
    value_raw: int
    value: str
    nonce: str


class ProofResponse(BaseModel):
    """链上 proof（06 §4）：该 provider 全部 Charged 交易哈希清单——每个数字可核。"""

    provider_wallet: str
    revenue_raw: int
    revenue: str
    count: int
    events: list[ProofEvent]
    explorer_url_base: str = EXPLORER_TX_BASE


# ---------------------------------------------------------------------------
# 端点内部装配
# ---------------------------------------------------------------------------


def _sync_quietly(request: Request) -> list[str]:
    """懒同步 Charged 库存（锁 + 最小间隔）；失败不抛，返回降级说明。"""
    state: dict[str, Any] = request.app.state.leaderboard_sync
    indexer: ChargedIndexer = request.app.state.charged_indexer
    degraded: list[str] = []
    with state["lock"]:
        now = time.monotonic()
        if now - state["last_ok_ts"] < state["min_interval"]:
            return degraded
        try:
            indexer.sync()
            state["last_ok_ts"] = now
        except Exception as exc:  # 排行榜不因链通道故障 500（06 §5-2 优雅降级）
            degraded.append(f"charged_sync_failed: {exc.__class__.__name__}")
    return degraded


def _gateway_stats(request: Request) -> tuple[dict[str, Any] | None, list[str]]:
    """活跃度源（网关）；不可达 → (None, 降级说明)，不阻塞收入侧返回。"""
    client: GatewayStatsSource = request.app.state.gateway_stats
    try:
        return client.stats_view(), []
    except Exception as exc:  # 网关宕机不拖垮排行榜（双源解耦）
        return None, [f"gateway_stats_failed: {exc.__class__.__name__}"]


def _per_service_calls(stats: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not stats:
        return {}
    return {row["service_id"]: row for row in stats.get("services", [])}


def _service_rows(request: Request, status: str | None) -> list[ServiceStatsRow]:
    store: CoreStore = request.app.state.store
    revenue_by_wallet = {r["provider"]: r for r in store.charged_by_provider()}
    provider_by_wallet = {p["wallet"]: p for p in store.list_providers()}
    stats, _degraded = _gateway_stats(request)
    calls_by_service = _per_service_calls(stats)
    rows: list[ServiceStatsRow] = []
    for svc in store.list_services(status):
        manifest = svc["manifest"]
        provider = manifest.get("provider", {})
        wallet = str(provider.get("wallet", "")).lower()
        revenue = revenue_by_wallet.get(wallet)
        agent_id = provider.get("agent_id")
        registered = provider_by_wallet.get(wallet)
        calls = calls_by_service.get(svc["service_id"])
        success = int(calls["calls_success"]) if calls else None
        aborted = int(calls["calls_aborted"]) if calls else None
        fail_rate: float | None = None
        if calls and success is not None and aborted is not None and (success + aborted) > 0:
            fail_rate = round(aborted / (success + aborted), 3)
        rows.append(
            ServiceStatsRow(
                service_id=svc["service_id"],
                name=str(manifest.get("name", "")),
                status=svc["status"],
                category=manifest_category(manifest),
                tags=manifest_tags(manifest),
                provider_wallet=wallet,
                provider_agent_id=int(agent_id) if agent_id is not None else None,
                display_name=(
                    registered["display_name"] if registered else provider.get("display_name")
                ),
                revenue_raw=int(revenue["revenue_raw"]) if revenue else 0,
                revenue=to_amount(int(revenue["revenue_raw"])) if revenue else "0",
                charged_count=int(revenue["charged_count"]) if revenue else 0,
                calls_success=success,
                calls_aborted=aborted,
                fail_rate=fail_rate,
                last_call_at=str(calls["last_call_at"]) if calls else None,
            )
        )
    rows.sort(key=lambda r: (-r.revenue_raw, -(r.calls_success or 0), r.service_id))
    return rows  # 降级说明仅在 overview 汇总透出，行内不重复


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/leaderboard/services", response_model=LeaderboardServicesResponse)
def leaderboard_services(request: Request) -> LeaderboardServicesResponse:
    """服务榜（06 §3）：收入优先降序；字段含活跃度（网关）与 fail_rate。"""
    _sync_quietly(request)
    rows = _service_rows(request, None)
    return LeaderboardServicesResponse(services=rows)


@router.get("/leaderboard/providers", response_model=LeaderboardProvidersResponse)
def leaderboard_providers(request: Request) -> LeaderboardProvidersResponse:
    """Provider 榜（06 §3）：链上 Charged 收入优先；零收入注册者垫底（不静默丢）。"""
    _sync_quietly(request)
    store: CoreStore = request.app.state.store
    revenue_rows = store.charged_by_provider()
    registered = {p["wallet"]: p for p in store.list_providers()}
    # Charged 键=manifest 收款钱包，与 /providers 登记的 agentWallet 天然分离：
    # 富化优先级 登记表 → manifest.provider（跨服务首个命中）→ 空（CONSTRAINTS §E 展示缺口补齐）
    manifest_info: dict[str, dict[str, object]] = {}
    for svc in store.list_services():
        provider = svc["manifest"].get("provider", {})
        wallet = str(provider.get("wallet", "")).lower()
        if wallet and wallet not in manifest_info:
            manifest_info[wallet] = {
                "display_name": provider.get("display_name"),
                "agent_id": provider.get("agent_id"),
            }
    rows: list[ProviderRevenueRow] = []
    seen: set[str] = set()
    for r in revenue_rows:
        wallet = r["provider"]
        info = registered.get(wallet) or manifest_info.get(wallet)
        rows.append(
            ProviderRevenueRow(
                wallet=wallet,
                display_name=info["display_name"] if info else None,
                agent_id=info["agent_id"] if info else None,
                revenue_raw=int(r["revenue_raw"]),
                revenue=to_amount(int(r["revenue_raw"])),
                charged_count=int(r["charged_count"]),
            )
        )
        seen.add(wallet)
    for wallet, p in registered.items():
        if wallet not in seen:
            rows.append(
                ProviderRevenueRow(
                    wallet=wallet,
                    display_name=p["display_name"],
                    agent_id=p["agent_id"],
                    revenue_raw=0,
                    revenue="0",
                    charged_count=0,
                )
            )
    rows.sort(key=lambda r: (-r.revenue_raw, -r.charged_count, r.wallet))
    return LeaderboardProvidersResponse(providers=rows)


@router.get("/store", response_model=StoreResponse)
def store_view(request: Request) -> StoreResponse:
    """商店页（01 §6）：active 服务 × 收入聚合（目录 + 大屏数据）。"""
    _sync_quietly(request)
    rows = _service_rows(request, "active")
    return StoreResponse(services=rows)


@router.get("/stats/overview", response_model=OverviewResponse)
def stats_overview(request: Request) -> OverviewResponse:
    """平台总览（06 §3，demo 大屏开场数字）：GMV=链上 Charged 总额（唯一真相）。"""
    degraded = _sync_quietly(request)
    store: CoreStore = request.app.state.store
    total = store.charged_total()
    stats, gw_degraded = _gateway_stats(request)
    degraded = list(degraded) + gw_degraded
    services = store.list_services()
    providers = store.list_providers()
    revenue_rows = store.charged_by_provider()
    totals = (stats or {}).get("totals", {})
    return OverviewResponse(
        gmv_raw=total["gmv_raw"],
        gmv=to_amount(total["gmv_raw"]),
        charged_count=total["charged_count"],
        calls_success_total=int(totals.get("calls_success") or 0),
        calls_aborted_total=int(totals.get("calls_aborted") or 0),
        services_total=len(services),
        services_active=sum(1 for s in services if s["status"] == "active"),
        providers_registered=len(providers),
        providers_with_revenue=len(revenue_rows),
        synced_to_block=store.get_watermark(STREAM_CHARGED),
        degraded=degraded,
    )


@router.get("/leaderboard/providers/{wallet}/proof", response_model=ProofResponse)
def provider_proof(wallet: str, request: Request) -> ProofResponse:
    """链上 proof（06 §4）：该 provider 全部 Charged 交易哈希清单（scan.bohr.life 可核）。"""
    normalized = wallet.lower()
    if not normalized.startswith("0x") or len(normalized) != WALLET_HEX_LEN:
        raise ApiError(
            status_code=422,
            error="invalid_request",
            detail=f"provider wallet 非法: {wallet!r}",
            code="bad_wallet",
        )
    _sync_quietly(request)
    store: CoreStore = request.app.state.store
    events = store.charged_events_for_provider(normalized)
    total = sum(ev["value_raw"] for ev in events)
    explorer_base: str = request.app.state.app_settings.explorer_tx_base
    return ProofResponse(
        provider_wallet=normalized,
        revenue_raw=total,
        revenue=to_amount(total),
        count=len(events),
        explorer_url_base=explorer_base,
        events=[
            ProofEvent(
                tx_hash=ev["tx_hash"],
                explorer_url=explorer_base + ev["tx_hash"],
                log_index=ev["log_index"],
                block_number=ev["block_number"],
                value_raw=ev["value_raw"],
                value=to_amount(ev["value_raw"]),
                nonce=ev["nonce"],
            )
            for ev in events
        ],
    )
