"""T19：MCP server 暴露五工具 catalog/service_quote/paid_service_call/spend_report/wallet_status。

标准 client 语义覆盖：initialize 握手 → tools/list → tools/call（成功/402 指引/
新三只读工具）；HTTP 侧复用 test_client 的 MockTransport 装配（A2 零网络），
链侧经 FakeChain 注入；另含真实 stdio 子进程冒烟（仅握手+列工具，不触网）。
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fake_chain import FakeChain
from test_client import ADVICE, ANVIL1_ADDR, ANVIL1_KEY, CATALOG, RECEIPT_HEADERS

from coincall.client import DEFAULT_CORE_URL, DEFAULT_GATEWAY_URL, Client
from coincall.errors import CoinCallError
from coincall.policy import PolicyConfig
from coincall.wallet import LocalWallet
from tools.mcp_server import PROTOCOL_VERSION, CoinCallMcpServer, build_client_from_env

REPO_ROOT = Path(__file__).resolve().parents[1]

FIVE_TOOLS = ["catalog", "service_quote", "paid_service_call", "spend_report", "wallet_status"]
_KEEP_DEFAULT = object()  # 哨兵：区分"未传 wallet"与"显式传 None（无钱包用例）"


def _mock_http() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "core.test":
            if request.url.path == "/advice":
                return httpx.Response(200, json=ADVICE)
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(200, json={"echo": "hi"}, headers=RECEIPT_HEADERS)

    return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)


def _server(
    wallet: object = _KEEP_DEFAULT,
    policy: PolicyConfig | None = None,
    http: httpx.Client | None = None,
) -> CoinCallMcpServer:
    """工厂注入 mock 客户端的 server（wallet/policy 可注入新三工具的用例）。"""

    effective_wallet = LocalWallet.from_key(ANVIL1_KEY) if wallet is _KEEP_DEFAULT else wallet

    def factory() -> Client:
        return Client(
            api_key="cck_test",
            wallet=effective_wallet,  # type: ignore[arg-type] —— 哨兵已消解，仅剩 None|LocalWallet
            gateway_url="http://gw.test",
            core_url="http://core.test",
            http=http or _mock_http(),
            policy=policy,
        )

    return CoinCallMcpServer(factory)


def _request(method: str, req_id: int | None = 1, **params: object) -> dict:
    body: dict = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        body["id"] = req_id
    if params:
        body["params"] = params
    return body


def _result(response: dict) -> dict:
    assert "error" not in response, response
    return response["result"]


@pytest.mark.unit
def test_initialize_handshake() -> None:
    server = _server()
    result = _result(server.handle_request(_request("initialize")) or {})
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "coincall-mcp"


@pytest.mark.unit
def test_list_tools_exposes_exactly_five_tools() -> None:
    server = _server()
    result = _result(server.handle_request(_request("tools/list")) or {})
    names = [tool["name"] for tool in result["tools"]]
    assert names == FIVE_TOOLS
    paid = result["tools"][2]
    assert paid["inputSchema"]["required"] == ["service_id", "params"]
    assert paid["inputSchema"]["properties"]["params"]["type"] == "object"
    # 使用纪律写死在 description 里（consumer-agent-interface §2：Agent 读描述学规矩）
    assert "付费动作" in paid["description"] and "不要自动重试" in paid["description"]
    assert "service_quote" in paid["description"]
    quote = result["tools"][1]
    assert quote["inputSchema"]["required"] == ["service_id"]


@pytest.mark.unit
def test_tool_catalog_returns_services() -> None:
    server = _server()
    result = _result(
        server.handle_request(_request("tools/call", name="catalog", arguments={})) or {}
    )
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["count"] == 1
    assert payload["services"][0]["service_id"] == "svc_e2e_demo"


@pytest.mark.unit
def test_tool_paid_service_call_success_with_receipt() -> None:
    server = _server()
    result = _result(
        server.handle_request(
            _request(
                "tools/call",
                name="paid_service_call",
                arguments={"service_id": "svc_e2e_demo", "params": {"text": "hi"}},
            )
        )
        or {}
    )
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["body"] == {"echo": "hi"}
    assert payload["receipt_id"] == "rcp_abc123"
    assert payload["charged_raw"] == "10000"


@pytest.mark.unit
def test_tool_paid_service_call_402_becomes_iserror_with_guidance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "core.test":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(
            402,
            json={
                "error": "payment_required",
                "detail": "钱包余额不足",
                "code": "insufficient_balance",
                "service_id": "svc_e2e_demo",
                "pricing": {"amount": "0.01", "amount_raw": "10000", "token": "USDT"},
                "wallet_balance_raw": "0",
                "payment": {"scheme": "erc3009-vault", "approve_to": "0xFe91"},
            },
        )

    def factory() -> Client:
        return Client(
            api_key="cck_test",
            wallet=LocalWallet.from_key(ANVIL1_KEY),
            gateway_url="http://gw.test",
            core_url="http://core.test",
            http=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False),
        )

    server = CoinCallMcpServer(factory)
    result = _result(
        server.handle_request(
            _request(
                "tools/call",
                name="paid_service_call",
                arguments={"service_id": "svc_e2e_demo", "params": {"text": "hi"}},
            )
        )
        or {}
    )
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "insufficient_balance" in text and "MockUSDT" in text  # 人话指引而非裸 JSON


@pytest.mark.unit
def test_unknown_tool_and_method_error_codes() -> None:
    server = _server()
    resp = server.handle_request(_request("tools/call", name="nope", arguments={})) or {}
    assert resp["error"]["code"] == -32602
    resp = server.handle_request(_request("bogus/method")) or {}
    assert resp["error"]["code"] == -32601


# -- 新三只读工具：service_quote / wallet_status / spend_report（决策闭环） --


def _policy(tmp_path: Path) -> PolicyConfig:
    return PolicyConfig(
        total_budget_raw=50000,
        daily_budget_raw=20000,
        max_per_call_raw=10000,
        state_path=tmp_path / "s.json",
        ledger_path=tmp_path / "l.jsonl",
    )


@pytest.mark.unit
def test_tool_service_quote_price_balance_budget(tmp_path: Path) -> None:
    wallet = LocalWallet.from_key(
        ANVIL1_KEY, chain=FakeChain(balance_raw=20000, allowance_raw=15000)
    )
    server = _server(wallet=wallet, policy=_policy(tmp_path))
    result = _result(
        server.handle_request(
            _request("tools/call", name="service_quote", arguments={"service_id": "svc_e2e_demo"})
        )
        or {}
    )
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["service_id"] == "svc_e2e_demo"
    assert payload["pricing"]["amount_raw"] == "10000" and payload["price_raw"] == 10000
    assert payload["payee"] == wallet.pay_vault  # 收款方锁死 PayVault
    assert payload["wallet"]["usdt_balance_raw"] == 20000
    assert payload["wallet"]["vault_allowance_raw"] == 15000
    assert payload["wallet"]["available_raw"] == 15000
    assert payload["affordable_by_wallet"] is True
    assert payload["budget"]["total_left_raw"] == 50000
    assert payload["affordable_by_budget"] is True
    assert payload["hints"] == []


@pytest.mark.unit
def test_tool_service_quote_unknown_service_iserror() -> None:
    server = _server()
    result = _result(
        server.handle_request(
            _request("tools/call", name="service_quote", arguments={"service_id": "svc_missing"})
        )
        or {}
    )
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "svc_missing" in text and "catalog" in text  # 人话指引而非裸 JSON


@pytest.mark.unit
def test_tool_service_quote_embeds_advice(tmp_path: Path) -> None:
    """默认通道：报价内嵌 /advice（category/current/L0 日额透传），工具描述带 switch 纪律句。"""
    advice_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "core.test":
            if request.url.path == "/advice":
                advice_requests.append(request)
                return httpx.Response(200, json=ADVICE)
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(200, json={"echo": "hi"}, headers=RECEIPT_HEADERS)

    wallet = LocalWallet.from_key(
        ANVIL1_KEY, chain=FakeChain(balance_raw=20000, allowance_raw=15000)
    )
    server = _server(
        wallet=wallet,
        policy=_policy(tmp_path),
        http=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False),
    )
    result = _result(
        server.handle_request(
            _request("tools/call", name="service_quote", arguments={"service_id": "svc_e2e_demo"})
        )
        or {}
    )
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["advice"]["verb"] == "keep"
    assert payload["advice"]["recommend"] == "svc_e2e_demo"
    assert payload["pricing"]["amount_raw"] == "10000"  # 报价本体原样
    # advice 请求形状：current=本服务 / category=manifest 类目 / daily=L0 日额
    assert len(advice_requests) == 1
    assert advice_requests[0].url.params["current"] == "svc_e2e_demo"
    assert advice_requests[0].url.params["category"] == "other"
    assert advice_requests[0].url.params["daily_budget_raw"] == "20000"
    # description 纪律句（工具面教学）
    tools = _result(server.handle_request(_request("tools/list")) or {})["tools"]
    desc = next(t for t in tools if t["name"] == "service_quote")["description"]
    assert "advice" in desc and "verb=switch 时改调 recommend" in desc


@pytest.mark.unit
def test_tool_service_quote_advice_degrades_without_hurting_quote() -> None:
    """失败降级：/advice 5xx 或 core 不可达 → advice={"error":"unavailable"}，报价本体不受影响。"""

    def make_http(fail_advice: bool) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "core.test":
                if request.url.path == "/advice":
                    if fail_advice:
                        return httpx.Response(
                            500,
                            json={"error": "server_error", "detail": "boom", "code": "internal"},
                        )
                    raise httpx.ConnectError("core down")  # 传输层不可达同路降级
                return httpx.Response(200, json=CATALOG)
            return httpx.Response(200, json={"echo": "hi"}, headers=RECEIPT_HEADERS)

        return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)

    for http in (make_http(True), make_http(False)):
        server = _server(http=http)
        result = _result(
            server.handle_request(
                _request(
                    "tools/call", name="service_quote", arguments={"service_id": "svc_e2e_demo"}
                )
            )
            or {}
        )
        assert result["isError"] is False  # advice 死活不影响报价
        payload = json.loads(result["content"][0]["text"])
        assert payload["advice"] == {"error": "unavailable"}
        assert payload["price_raw"] == 10000 and payload["payee"].startswith("0x")


@pytest.mark.unit
def test_tool_service_quote_low_balance_hints(tmp_path: Path) -> None:
    wallet = LocalWallet.from_key(ANVIL1_KEY, chain=FakeChain(balance_raw=0, allowance_raw=0))
    server = _server(wallet=wallet, policy=_policy(tmp_path))
    result = _result(
        server.handle_request(
            _request("tools/call", name="service_quote", arguments={"service_id": "svc_e2e_demo"})
        )
        or {}
    )
    payload = json.loads(result["content"][0]["text"])
    assert payload["affordable_by_wallet"] is False
    assert any("mint" in h for h in payload["hints"])  # 余额 0 → 水龙头指引
    assert any("approve" in h for h in payload["hints"])  # 授权 0 → approve 指引


@pytest.mark.unit
def test_tool_wallet_status_zero_balance_hints(tmp_path: Path) -> None:
    wallet = LocalWallet.from_key(ANVIL1_KEY, chain=FakeChain(balance_raw=0, allowance_raw=0))
    server = _server(wallet=wallet, policy=_policy(tmp_path))
    result = _result(
        server.handle_request(_request("tools/call", name="wallet_status", arguments={})) or {}
    )
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["address"] == ANVIL1_ADDR
    assert payload["chain_id"] == wallet.chain_id
    assert payload["usdt_balance_raw"] == 0 and payload["vault_allowance_raw"] == 0
    assert "mint" in payload["hint_fund_wallet"]  # 水龙头人话
    assert "approve_vault" in payload["hint_approve_vault"]
    assert payload["policy"]["daily_budget_raw"] == 20000
    assert "hint_no_wallet" not in payload


@pytest.mark.unit
def test_tool_wallet_status_no_wallet_hint() -> None:
    server = _server(wallet=None)
    result = _result(
        server.handle_request(_request("tools/call", name="wallet_status", arguments={})) or {}
    )
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["address"] is None
    assert "COINCALL_WALLET_KEY" in payload["hint_no_wallet"]
    assert payload["policy"] is None  # 未装配 L0 时如实报告


@pytest.mark.unit
def test_tool_spend_report_summary_and_recent(tmp_path: Path) -> None:
    server = _server(policy=_policy(tmp_path))
    client = server.client()
    assert client.policy is not None
    client.policy.check("svc_e2e_demo", 10000)  # 记一笔预算
    rid = client.policy.record_intent("svc_e2e_demo", 10000, auth_nonce="0x" + "ab" * 32)
    client.policy.record_receipt(rid, "rcp_x", charged_raw=10000)

    result = _result(
        server.handle_request(_request("tools/call", name="spend_report", arguments={})) or {}
    )
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["policy"]["total_spent_raw"] == 10000
    assert payload["policy"]["total_left_raw"] == 40000
    assert payload["recent"][0]["stage"] == "receipt"  # 最新在前
    assert payload["recent"][0]["gateway_receipt"] == "rcp_x"
    assert payload["spent_in_session_raw"] == 0  # 本次进程未成功调用


@pytest.mark.unit
def test_tool_spend_report_without_policy_hint() -> None:
    server = _server()
    result = _result(
        server.handle_request(_request("tools/call", name="spend_report", arguments={})) or {}
    )
    payload = json.loads(result["content"][0]["text"])
    assert payload["policy"] is None
    assert "COINCALL_" in payload["hint_no_policy"]  # 告诉用户怎么开 L0
    assert payload["recent"] == []


@pytest.mark.unit
def test_notification_and_ping() -> None:
    server = _server()
    assert server.handle_request(_request("notifications/initialized", req_id=None)) is None
    result = _result(server.handle_request(_request("ping")) or {})
    assert result == {}


@pytest.mark.unit
def test_stdio_serve_round_trip() -> None:
    server = _server()
    reader = io.StringIO(
        json.dumps(_request("initialize", req_id=7))
        + "\n"
        + json.dumps(_request("notifications/initialized", req_id=None))
        + "\n"
        + "not-json\n"
        + json.dumps(_request("tools/list", req_id=8))
        + "\n"
    )
    writer = io.StringIO()
    server.serve(reader, writer)
    lines = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert len(lines) == 3  # notification 不回帧；坏行回 -32700
    assert lines[0]["id"] == 7 and lines[0]["result"]["serverInfo"]["name"] == "coincall-mcp"
    assert lines[1]["error"]["code"] == -32700
    assert lines[2]["id"] == 8 and len(lines[2]["result"]["tools"]) == 5


@pytest.mark.unit
def test_build_client_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COINCALL_API_KEY", "cck_env")
    monkeypatch.setenv("COINCALL_WALLET_KEY", ANVIL1_KEY)
    monkeypatch.setenv("COINCALL_BUDGET_RAW", "50000")
    monkeypatch.setenv("COINCALL_GATEWAY_URL", "http://gw:8030/")
    monkeypatch.setenv("COINCALL_CORE_URL", "http://core:8020/")
    client = build_client_from_env()
    assert client.api_key == "cck_env"
    assert client.wallet is not None and client.wallet.address.startswith("0x")
    assert client.budget_raw == 50000
    assert client.gateway_url == "http://gw:8030"
    assert client.core_url == "http://core:8020"


@pytest.mark.unit
def test_build_client_from_env_defaults_and_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "COINCALL_API_KEY",
        "COINCALL_WALLET_KEY",
        "COINCALL_BUDGET_RAW",
        "COINCALL_GATEWAY_URL",
        "COINCALL_CORE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    client = build_client_from_env()
    assert client.api_key == ""
    assert client.gateway_url == DEFAULT_GATEWAY_URL
    assert client.core_url == DEFAULT_CORE_URL
    assert client.budget_raw is None and client.wallet is None  # catalog-only 降级
    # 钱包来源非法：装配期即报人话错误（WalletError 属 CoinCallError 族）
    monkeypatch.setenv("COINCALL_WALLET_KEY", "/nonexistent/wallet.key")
    with pytest.raises(CoinCallError, match="私钥"):
        build_client_from_env()


@pytest.mark.unit
def test_subprocess_stdio_list_tools() -> None:
    """真实 stdio 冒烟：标准 client 可拉起 server 并 list_tools（不触网）。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("COINCALL_")}
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(  # noqa: S603 —— 本仓 venv python 执行本仓脚本，输入受控
        [sys.executable, str(REPO_ROOT / "tools" / "mcp_server.py")],
        input=(
            json.dumps(_request("initialize", req_id=1))
            + "\n"
            + json.dumps(_request("tools/list", req_id=2))
            + "\n"
        ),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    tools_frame = next(line for line in lines if line.get("id") == 2)
    names = [tool["name"] for tool in tools_frame["result"]["tools"]]
    assert names == FIVE_TOOLS
