#!/usr/bin/env python3
"""CoinCall MCP server（stdio）——任何 Agent 框架挂载即接入（03 §6）。

两个工具：
  - catalog            列出平台可用付费服务（机读目录）
  - paid_service_call  调用付费服务（SDK 组装 EIP-712 支付授权 + X-PAYMENT）

环境变量（全部必填项缺省时给出人话指引）：
  COINCALL_API_KEY       core 签发的 api key（POST /apikeys，仅回显一次）
  COINCALL_GATEWAY_URL   网关地址（默认 http://127.0.0.1:8030）
  COINCALL_CORE_URL      core 地址（默认 http://127.0.0.1:8020）
  COINCALL_WALLET_KEY    付费钱包私钥：0x hex 或 0600 key 文件路径
  COINCALL_BUDGET_RAW    本地预算上限（最小单位；超出即拒绝调用）

stdio 帧：每行一个 JSON-RPC 2.0 消息（MCP stdio transport）。
日志只写 stderr（stdout 是协议通道）。
"""

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

from coincall.policy import PolicyConfig

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coincall.client import DEFAULT_CORE_URL, DEFAULT_GATEWAY_URL, CallResult, Client
from coincall.errors import CoinCallError
from coincall.wallet import LocalWallet

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "coincall-mcp"
SERVER_VERSION = "0.1.0"

ENV_API_KEY = "COINCALL_API_KEY"
ENV_GATEWAY_URL = "COINCALL_GATEWAY_URL"
ENV_CORE_URL = "COINCALL_CORE_URL"
ENV_WALLET_KEY = "COINCALL_WALLET_KEY"


def _policy_from_env() -> PolicyConfig:
    """L0 策略 env 装配（agent-wallet-trust §3）：三重预算/白名单/速率。"""
    return PolicyConfig.from_env()


ENV_BUDGET_RAW = "COINCALL_BUDGET_RAW"

# L0 策略引擎（agent-wallet-trust.md §3）：三重预算/白名单/速率，经 Client 生效
ENV_POLICY_DAILY = "COINCALL_DAILY_BUDGET_RAW"
ENV_POLICY_PER_CALL = "COINCALL_PER_CALL_BUDGET_RAW"
ENV_POLICY_ALLOWED = "COINCALL_ALLOWED_SERVICES"
ENV_POLICY_MIN_INTERVAL = "COINCALL_MIN_INTERVAL_S"
ENV_POLICY_MAX_HOURLY = "COINCALL_MAX_CALLS_PER_HOUR"

TOOLS_SPEC: list[dict[str, Any]] = [
    {
        "name": "catalog",
        "description": (
            "列出 CoinCall 平台可用的付费 Agent 服务（服务 ID、定价、输入 schema）。"
            "调用任何付费服务前先用本工具查目录。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "paid_service_call",
        "description": (
            "调用平台目录中的付费 Agent 服务并返回结果（自动组装 EIP-712 支付授权；"
            "余额/授权不足时返回人话指引）。先 catalog 查可用服务与定价。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "description": "目录中的服务 ID"},
                "params": {"type": "object", "description": "该服务 input_schema 定义的请求体"},
            },
            "required": ["service_id", "params"],
        },
    },
]


def build_client_from_env() -> Client:
    """从环境变量装配 Client。

    api key 缺省为空串：catalog 可用；付费调用将由网关 402(missing_api_key)
    给出人话指引（不在装配期硬失败，保住"挂载即 list_tools"的体验）。
    """
    wallet: LocalWallet | None = None
    if os.environ.get(ENV_WALLET_KEY, ""):
        wallet = LocalWallet.from_key(os.environ[ENV_WALLET_KEY])
    budget_raw: int | None = None
    if os.environ.get(ENV_BUDGET_RAW, ""):
        budget_raw = int(os.environ[ENV_BUDGET_RAW])
    policy = _policy_from_env()
    policy.total_budget_raw = policy.total_budget_raw or budget_raw  # BUDGET_RAW 兼容映射总额
    return Client(
        api_key=os.environ.get(ENV_API_KEY, ""),
        wallet=wallet,
        gateway_url=os.environ.get(ENV_GATEWAY_URL, DEFAULT_GATEWAY_URL),
        core_url=os.environ.get(ENV_CORE_URL, DEFAULT_CORE_URL),
        budget_raw=budget_raw,
        policy=policy,
    )


@dataclass(frozen=True)
class _ToolOutcome:
    text: str
    is_error: bool = False


class CoinCallMcpServer:
    """极简 MCP stdio server：initialize / tools/list / tools/call / ping。"""

    def __init__(self, client_factory: Callable[[], Client]) -> None:
        self._client_factory = client_factory
        self._client: Client | None = None

    def client(self) -> Client:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    # -- JSON-RPC 分发 --

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:  # noqa: PLR0911 —— JSON-RPC 分发器天然多分支
        """处理一帧请求；通知（无 id）与未知通知返回 None（不回帧）。"""
        method = str(request.get("method", ""))
        req_id = request.get("id")
        try:
            if method == "initialize":
                result: Any = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS_SPEC}
            elif method == "tools/call":
                params = request.get("params") or {}
                tool_name = str(params.get("name", ""))
                if tool_name not in {"catalog", "paid_service_call"}:
                    if req_id is None:
                        return None
                    return self._error(
                        req_id,
                        -32602,
                        f"unknown tool: {tool_name}（可用: catalog / paid_service_call）",
                    )
                outcome = self._dispatch_tool(tool_name, params.get("arguments") or {})
                result = {
                    "content": [{"type": "text", "text": outcome.text}],
                    "isError": outcome.is_error,
                }
            elif method.startswith("notifications/"):
                return None  # 通知不回帧
            elif req_id is not None:
                return self._error(req_id, -32601, f"method not found: {method}")
            else:
                return None
        except CoinCallError as exc:
            if req_id is None:
                print(f"[coincall-mcp] {exc}", file=sys.stderr)
                return None
            return self._error(req_id, -32000, str(exc))
        return self._response(req_id, result)

    def _dispatch_tool(self, name: str, arguments: dict[str, Any]) -> _ToolOutcome:
        """两工具分发；CoinCallError 一律转 isError 文本（含 402 人话指引）。"""
        try:
            if name == "catalog":
                return _ToolOutcome(
                    json.dumps(self.client().catalog(), ensure_ascii=False, default=str)
                )
            if name == "paid_service_call":
                service_id = str(arguments.get("service_id", ""))
                params = arguments.get("params")
                if not service_id or not isinstance(params, dict):
                    return _ToolOutcome(
                        "参数错误：需要 service_id (str) 与 params (object)", is_error=True
                    )
                result: CallResult = self.client().call(service_id, params)
                return _ToolOutcome(
                    json.dumps(
                        {
                            "service_id": result.service_id,
                            "status_code": result.status_code,
                            "body": result.body,
                            "receipt_id": result.receipt_id,
                            "charged_raw": result.charged_raw,
                            "receipt_sig": result.receipt_sig,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                )
            return _ToolOutcome(
                f"未知工具: {name}（可用: catalog / paid_service_call）", is_error=True
            )
        except CoinCallError as exc:
            return _ToolOutcome(str(exc), is_error=True)

    # -- 帧构造 --

    @staticmethod
    def _response(req_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    # -- stdio 主循环 --

    def serve(self, reader: TextIO, writer: TextIO) -> None:
        """逐行读 JSON-RPC；坏行回 -32700；响应单行写回（stdout 仅协议帧）。"""
        for raw_line in reader:
            frame = raw_line.strip()
            if not frame:
                continue
            try:
                request = json.loads(frame)
                if not isinstance(request, dict):
                    raise TypeError("帧必须是 JSON 对象")
            except (ValueError, TypeError) as exc:
                response: dict[str, Any] | None = self._error(None, -32700, f"parse error: {exc}")
            else:
                response = self.handle_request(request)
            if response is not None:
                writer.write(json.dumps(response, ensure_ascii=False) + "\n")
                writer.flush()


def main() -> None:
    server = CoinCallMcpServer(build_client_from_env)  # 客户端惰性装配（首个工具调用时）
    server.serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
