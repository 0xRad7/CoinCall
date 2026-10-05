"""T19：MCP server 暴露 catalog / paid_service_call 两工具（03 §6）。

标准 client 语义覆盖：initialize 握手 → tools/list → tools/call（成功/402 指引）；
HTTP 侧复用 test_client 的 MockTransport 装配（A2 零网络）；
另含真实 stdio 子进程冒烟（仅握手+列工具，不触网）。
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from test_client import ANVIL1_KEY, CATALOG, RECEIPT_HEADERS

from coincall.client import DEFAULT_CORE_URL, DEFAULT_GATEWAY_URL, Client
from coincall.errors import CoinCallError
from coincall.wallet import LocalWallet
from tools.mcp_server import PROTOCOL_VERSION, CoinCallMcpServer, build_client_from_env

REPO_ROOT = Path(__file__).resolve().parents[1]


def _mock_http() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "core.test":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(200, json={"echo": "hi"}, headers=RECEIPT_HEADERS)

    return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)


def _server() -> CoinCallMcpServer:
    """工厂注入 mock 客户端的 server。"""

    def factory() -> Client:
        return Client(
            api_key="cck_test",
            wallet=LocalWallet.from_key(ANVIL1_KEY),
            gateway_url="http://gw.test",
            core_url="http://core.test",
            http=_mock_http(),
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
def test_list_tools_exposes_exactly_two_tools() -> None:
    server = _server()
    result = _result(server.handle_request(_request("tools/list")) or {})
    names = [tool["name"] for tool in result["tools"]]
    assert names == ["catalog", "paid_service_call"]
    paid = result["tools"][1]
    assert paid["inputSchema"]["required"] == ["service_id", "params"]
    assert paid["inputSchema"]["properties"]["params"]["type"] == "object"


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
    assert lines[2]["id"] == 8 and len(lines[2]["result"]["tools"]) == 2


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
    assert names == ["catalog", "paid_service_call"]
