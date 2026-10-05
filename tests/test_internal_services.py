"""P1-4 内置 demo 服务单测：internal:// 分发 + 三 handler（8010/8020 全 mock，零网络）。

覆盖：handler_name 解析规则 / TranslateHandler 回显形态 / ChainReportHandler 报告组装
与上游故障→ProviderError / ContractScanHandler Charged 解码与窗口钳制 /
InternalServicesProvider 分发与回显兜底 / 经 /call 路由的 200 与 aborted 零扣款（A7）。
"""

import json
from typing import Any

import httpx
import pytest
import respx

from app.modules.internal_services import (
    CHARGED_TOPIC0,
    ContractScanHandler,
    InternalServiceError,
    InternalServicesProvider,
    TranslateHandler,
    handler_name,
)
from app.modules.manifest_client import ManifestInfo
from app.modules.providers import ProviderError

pytestmark = pytest.mark.unit

BOTCHAIN = "http://botchain.test"
CORE = "http://core.test"
VAULT = "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471"
PROVIDER_WALLET = "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"
CONSUMER_WALLET = "0x70997970c51812dc3a010c7d01c50f11d17c20c8"
TX = "0x" + "ab" * 32

HEALTH_BODY = {
    "ok": True,
    "channels": {
        "rpc": {"ok": True, "latency_ms": 120, "detail": "chain_id=968"},
        "explorer": {"ok": True, "latency_ms": 200, "detail": "scan ok"},
        "bundler": {"ok": False, "latency_ms": 5000, "detail": "timeout"},
    },
}

OVERVIEW_BODY = {
    "gmv_raw": 150000,
    "gmv": "0.15",
    "charged_count": 15,
    "calls_success_total": 20,
    "calls_aborted_total": 1,
    "services_total": 4,
    "services_active": 3,
    "providers_registered": 2,
    "providers_with_revenue": 1,
    "synced_to_block": 25_804_306,
    "degraded": [],
}


def make_manifest(
    service_id: str = "svc_translate", endpoint: dict[str, Any] | None = None
) -> ManifestInfo:
    from tests.conftest import make_manifest as base_make

    return base_make(service_id, endpoint=endpoint or {"type": "internal"})


def charged_log(value_raw: int, block: int, tx: str = TX) -> dict[str, Any]:
    def topic(addr: str) -> str:
        return "0x" + "00" * 12 + addr.removeprefix("0x")

    return {
        "address": VAULT.lower(),
        "topics": [
            CHARGED_TOPIC0,
            topic(PROVIDER_WALLET),
            topic(CONSUMER_WALLET),
            "0x" + "11" * 32,
        ],
        "data": "0x" + f"{value_raw:064x}",
        "block_number": block,
        "block_hash": "0x" + "cd" * 32,
        "transaction_hash": tx,
        "transaction_index": 1,
        "log_index": 2,
        "removed": False,
    }


# ---- handler_name 解析（07 §2：internal:// 前缀网关本地执行） ----


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("internal://translate", "translate"),
        ("internal://contract_scan", "contract_scan"),
        ("internal://chain_report/", "chain_report"),
        ("internal://", None),
        ("https://team.example/translate", None),
        (None, None),
        ("", None),
    ],
)
def test_handler_name_parsing(url: str | None, expected: str | None) -> None:
    assert handler_name(url) == expected


# ---- TranslateHandler ----


async def test_translate_handler_echo_shape() -> None:
    result = await TranslateHandler().handle(
        make_manifest(endpoint={"type": "internal", "url": "internal://translate"}),
        {"text": "settlement is verifiable"},
    )
    assert result.status_code == 200
    assert result.body["service_id"] == "svc_translate"
    assert result.body["text"] == "settlement is verifiable"
    assert "settlement is verifiable" in result.body["translated"]
    assert result.body["engine"] == "internal-fallback"


async def test_translate_handler_non_dict_body_safe() -> None:
    result = await TranslateHandler().handle(make_manifest(), ["not", "a", "dict"])
    assert result.status_code == 200
    assert result.body["text"] is None


# ---- ChainReportHandler ----


@respx.mock
async def test_chain_report_handler_builds_report() -> None:
    respx.get(f"{BOTCHAIN}/api/v1/chain/health").mock(
        return_value=httpx.Response(200, json=HEALTH_BODY)
    )
    respx.get(f"{CORE}/stats/overview").mock(return_value=httpx.Response(200, json=OVERVIEW_BODY))
    async with httpx.AsyncClient() as http:
        handler = _chain_report(http)
        result = await handler.handle(make_manifest("svc_chain_report"), {})
    assert result.status_code == 200
    body = result.body
    # 报告文本摘要有链通道与平台数字
    assert "rpc: ok (120ms)" in body["report"]
    assert "bundler: DOWN (5000ms)" in body["report"]
    assert "GMV 0.15 USDT" in body["report"]
    assert "Charged 15 笔" in body["report"]
    assert "总调用 20" in body["report"]
    # 结构化透传（Agent 机读）
    assert body["chain_health"] == HEALTH_BODY
    assert body["platform_overview"] == OVERVIEW_BODY
    assert body["service_id"] == "svc_chain_report"


@respx.mock
async def test_chain_report_handler_upstream_500_raises() -> None:
    respx.get(f"{BOTCHAIN}/api/v1/chain/health").mock(return_value=httpx.Response(502))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderError):
            await _chain_report(http).handle(make_manifest("svc_chain_report"), {})


@respx.mock
async def test_chain_report_handler_unreachable_raises() -> None:
    respx.get(f"{BOTCHAIN}/api/v1/chain/health").mock(side_effect=httpx.ConnectError("refused"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderError):
            await _chain_report(http).handle(make_manifest("svc_chain_report"), {})


def _chain_report(http: httpx.AsyncClient) -> Any:
    from app.modules.internal_services import ChainReportHandler

    return ChainReportHandler(http, BOTCHAIN, CORE)


# ---- ContractScanHandler ----


@respx.mock
async def test_contract_scan_handler_decodes_charged_logs() -> None:
    info_route = respx.get(f"{BOTCHAIN}/api/v1/chain/info").mock(
        return_value=httpx.Response(200, json={"block_number": 1000, "chain_id": 968})
    )
    logs_route = respx.get(f"{BOTCHAIN}/api/v1/contracts/logs").mock(
        return_value=httpx.Response(
            200,
            json={
                "address": VAULT,
                "topic0": CHARGED_TOPIC0,
                "from_block": 700,
                "to_block": 1000,
                "count": 2,
                "truncated": False,
                "logs": [charged_log(10_000, 800), charged_log(50_000, 900, tx="0x" + "ef" * 32)],
            },
        )
    )
    async with httpx.AsyncClient() as http:
        handler = ContractScanHandler(http, BOTCHAIN, VAULT)
        result = await handler.handle(make_manifest("svc_contract_scan"), {"window_blocks": 300})
    assert result.status_code == 200
    body = result.body
    assert body["count"] == 2
    assert body["total_value_raw"] == 60_000
    assert body["total_value"] == "0.060000"
    assert body["pay_vault"] == VAULT
    assert body["window"] == {"from_block": 700, "to_block": 1000, "blocks": 301}
    first = body["events"][0]
    assert first["provider"] == PROVIDER_WALLET
    assert first["from"] == CONSUMER_WALLET
    assert first["value_raw"] == 10_000
    assert first["value"] == "0.010000"
    assert first["tx_hash"] == TX
    assert first["nonce"] == "0x" + "11" * 32
    # 8010 请求形态：地址 + topic0 过滤，窗口按 tip-300
    assert info_route.called
    sent = dict(logs_route.calls.last.request.url.params)
    assert sent["address"].lower() == VAULT.lower()
    assert sent["topic0"].lower() == CHARGED_TOPIC0.lower()
    assert int(sent["from_block"]) == 700
    assert int(sent["to_block"]) == 1000


@respx.mock
async def test_contract_scan_handler_window_clamped() -> None:
    respx.get(f"{BOTCHAIN}/api/v1/chain/info").mock(
        return_value=httpx.Response(200, json={"block_number": 6000})
    )
    route = respx.get(f"{BOTCHAIN}/api/v1/contracts/logs").mock(
        return_value=httpx.Response(200, json={"logs": []})
    )
    async with httpx.AsyncClient() as http:
        handler = ContractScanHandler(http, BOTCHAIN, VAULT)
        result = await handler.handle(
            make_manifest("svc_contract_scan"), {"window_blocks": 999_999}
        )
    sent = dict(route.calls.last.request.url.params)
    assert int(sent["from_block"]) == 6000 - 5000  # 8010 窗口硬顶 5000
    assert result.body["count"] == 0
    assert result.body["total_value_raw"] == 0


@respx.mock
async def test_contract_scan_handler_default_window_and_bad_input() -> None:
    respx.get(f"{BOTCHAIN}/api/v1/chain/info").mock(
        return_value=httpx.Response(200, json={"block_number": 1000})
    )
    route = respx.get(f"{BOTCHAIN}/api/v1/contracts/logs").mock(
        return_value=httpx.Response(200, json={"logs": []})
    )
    async with httpx.AsyncClient() as http:
        handler = ContractScanHandler(http, BOTCHAIN, VAULT)
        # 非法 window_blocks（字符串/负数）→ 缺省 300，不炸
        await handler.handle(make_manifest("svc_contract_scan"), {"window_blocks": "abc"})
        await handler.handle(make_manifest("svc_contract_scan"), {"window_blocks": -5})
    for call in route.calls:
        assert int(dict(call.request.url.params)["from_block"]) == 700


@respx.mock
async def test_contract_scan_handler_upstream_fail_raises() -> None:
    respx.get(f"{BOTCHAIN}/api/v1/chain/info").mock(side_effect=httpx.ReadTimeout("t/o"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderError):
            await ContractScanHandler(http, BOTCHAIN, VAULT).handle(
                make_manifest("svc_contract_scan"), {}
            )


# ---- InternalServicesProvider 分发 ----


class _RecordingHandler:
    name = "recording"

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    async def handle(self, manifest: Any, body: Any) -> Any:
        from app.modules.providers import ProviderResult

        self.calls.append({"service_id": manifest.service_id, "body": body})
        if self.error is not None:
            raise self.error
        return ProviderResult(status_code=200, body={"routed": True})


async def test_services_provider_routes_internal_url() -> None:
    handler = _RecordingHandler()
    provider = InternalServicesProvider(handlers={"recording": handler})
    result = await provider.forward(
        make_manifest(endpoint={"type": "internal", "url": "internal://recording"}),
        {"text": "hi"},
    )
    assert result.body == {"routed": True}
    assert handler.calls == [{"service_id": "svc_translate", "body": {"text": "hi"}}]


async def test_services_provider_unknown_name_falls_back_to_echo() -> None:
    provider = InternalServicesProvider(handlers={})
    result = await provider.forward(
        make_manifest(endpoint={"type": "internal", "url": "internal://nope"}), {"text": "hi"}
    )
    assert "echo" in result.body


async def test_services_provider_no_url_falls_back_to_echo() -> None:
    provider = InternalServicesProvider(handlers={})
    result = await provider.forward(make_manifest(endpoint={"type": "internal"}), {"x": 1})
    assert result.body["echo"] == {"x": 1}


async def test_services_provider_non_internal_url_falls_back_to_echo() -> None:
    """http_json 形态的 url 误配 internal 适配器时：不外联，回显兜底（demo 网络单点规则）。"""
    provider = InternalServicesProvider(handlers={})
    result = await provider.forward(
        make_manifest(endpoint={"type": "http_json", "url": "https://x.example/t"}), {"x": 1}
    )
    assert "echo" in result.body


async def test_services_provider_handler_error_is_provider_error() -> None:
    boom = _RecordingHandler(error=InternalServiceError("8010 down"))
    provider = InternalServicesProvider(handlers={"recording": boom})
    with pytest.raises(ProviderError):
        await provider.forward(
            make_manifest(endpoint={"type": "internal", "url": "internal://recording"}), {}
        )


# ---- 经 /call 路由（7 步时序）集成 ----


async def test_call_route_internal_translate_200(settings: object) -> None:
    from tests.conftest import FakeManifests, call_headers, gateway_serve, make_manifest

    manifest = make_manifest(
        "svc_translate", endpoint={"type": "internal", "url": "internal://translate"}
    )
    translate = InternalServicesProvider(handlers={"translate": TranslateHandler()})
    async with gateway_serve(
        settings,  # type: ignore[arg-type]
        manifests=FakeManifests({"svc_translate": manifest}),
        providers={"internal": translate},
    ) as (client, app):
        resp = await client.post(
            "/call/svc_translate",
            headers=call_headers(),
            json={"text": "paid internal call"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["engine"] == "internal-fallback"
        assert resp.headers["X-Charged-Raw"] == "10000"
        assert resp.headers["X-Receipt-Id"].startswith("rcp_")
        store = app.state.store
        row = store.conn.execute("SELECT status FROM calls").fetchone()
        assert row is not None and row[0] == "success"


@respx.mock
async def test_call_route_internal_downstream_fail_aborts_zero_charge(settings: object) -> None:
    """internal 依赖（8010）挂掉 → 502 provider_failed、calls=aborted、零 settle（A7）。"""
    from app.modules.internal_services import ChainReportHandler
    from tests.conftest import FakeManifests, call_headers, gateway_serve, make_manifest

    respx.get("http://botchain.test/api/v1/chain/health").mock(return_value=httpx.Response(500))
    manifest = make_manifest(
        "svc_chain_report",
        endpoint={"type": "internal", "url": "internal://chain_report"},
        input_schema={"type": "object"},  # 链报告无必填参数（schema 校验不拦 ③ 步）
    )
    async with httpx.AsyncClient() as http:
        provider = InternalServicesProvider(
            handlers={"chain_report": ChainReportHandler(http, BOTCHAIN, CORE)}
        )
        async with gateway_serve(
            settings,  # type: ignore[arg-type]
            manifests=FakeManifests({"svc_chain_report": manifest}),
            providers={"internal": provider},
        ) as (client, app):
            resp = await client.post("/call/svc_chain_report", headers=call_headers(), json={})
            assert resp.status_code == 502
            assert resp.json()["code"] == "provider_failed"
            store = app.state.store
            row = store.conn.execute("SELECT status FROM calls").fetchone()
            assert row is not None and row[0] == "aborted"
            assert store.pending_settles(limit=10) == []


def test_charged_topic0_matches_contract_signature() -> None:
    """Charged(address,address,uint256,bytes32) 的 keccak——与链上事件对齐的静态锚。"""
    from eth_utils import keccak

    expected = "0x" + keccak(text="Charged(address,address,uint256,bytes32)").hex()
    assert expected == CHARGED_TOPIC0


async def test_internal_json_serializable_body() -> None:
    """demo 大屏需要 json.dumps 收据体：handler 产物必须可序列化。"""
    result = await TranslateHandler().handle(make_manifest(), {"text": "x"})
    json.dumps(result.body)
