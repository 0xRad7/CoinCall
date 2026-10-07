#!/usr/bin/env python3
"""CoinCall MCP server（stdio）——任何 Agent 框架挂载即接入（03 §6 / consumer-agent-interface §2）。

五个工具（Agent 决策闭环：自查 → 看目录 → 看价 → 付费 → 汇报）：
  - wallet_status      钱包健康自查：地址/链/USDT 余额/对 PayVault 授权/L0 各限额现值
  - catalog            列出平台可用付费服务（机读目录）
  - service_quote      单服务报价：定价/收款方/自己的余额与授权/L0 预算余量
  - paid_service_call  调用付费服务（唯一花钱工具；SDK 组装 EIP-712 支付授权 + X-PAYMENT）
  - spend_report       本地账本聚合：已花/剩余/最近 N 笔（intent→receipt→onchain）

环境变量（全部必填项缺省时给出人话指引）：
  COINCALL_API_KEY       core 签发的 api key（POST /apikeys，仅回显一次）
  COINCALL_GATEWAY_URL   网关地址（默认 http://127.0.0.1:8030）
  COINCALL_CORE_URL      core 地址（默认 http://127.0.0.1:8020）
  COINCALL_WALLET_KEY    付费钱包私钥：0x hex 或 0600 key 文件路径
  COINCALL_BUDGET_RAW    本地预算上限（最小单位；超出即拒绝调用）
  COINCALL_{TOTAL,DAILY,PER_CALL}_BUDGET_RAW / COINCALL_ALLOWED_SERVICES /
  COINCALL_MIN_INTERVAL_S / COINCALL_MAX_CALLS_PER_HOUR   L0 策略五变量

stdio 帧：每行一个 JSON-RPC 2.0 消息（MCP stdio transport）。
日志只写 stderr（stdout 是协议通道）。
"""

# ruff: noqa: T201, ANN401 —— stdio 协议通道 print 是本体；JSON-RPC 字段天然 Any
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

import httpx

from coincall.client import DEFAULT_CORE_URL, DEFAULT_GATEWAY_URL, CallResult, Client
from coincall.errors import CoinCallError
from coincall.policy import PolicyConfig
from coincall.signing import PAY_VAULT_ADDRESS
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

# 工具面（consumer-agent-interface.md §2）：五件套 + 分发白名单
TOOL_NAMES_ORDERED = [
    "catalog",
    "service_quote",
    "paid_service_call",
    "spend_report",
    "wallet_status",
]
TOOL_NAMES = set(TOOL_NAMES_ORDERED)

TOOLS_SPEC: list[dict[str, Any]] = [
    {
        "name": "catalog",
        "description": (
            "列出 CoinCall 平台可用的付费 Agent 服务（服务 ID、定价、输入 schema）。"
            "免费。调用任何付费服务前先用本工具查目录。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "service_quote",
        "description": (
            "单服务报价详情（免费）：目录定价、收款方 PayVault、你的钱包 USDT 余额与"
            "授权额度、L0 预算余量。Agent 花钱前先本工具看价；价格超单笔限额、"
            "余额/授权/预算不足时，把返回的 hints 转告用户并等待指示，不要直接调用。"
            "返回含 advice 建议：verb=switch 时改调 recommend 并向用户说明理由。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"service_id": {"type": "string", "description": "目录中的服务 ID"}},
            "required": ["service_id"],
        },
    },
    {
        "name": "paid_service_call",
        "description": (
            "**付费动作**。调用平台目录中的付费 Agent 服务并返回结果（自动组装 EIP-712 "
            "支付授权；余额/授权不足时返回人话指引）。调用前先 catalog 确认 service_id "
            "与定价，用 service_quote 核对余额/预算。失败会返回人话指引；"
            "**不要自动重试付费调用**（可能是恶意循环）——把指引转告用户。"
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
    {
        "name": "spend_report",
        "description": (
            "本地账本聚合（免费）：L0 已花/剩余（总额+日额）与最近 N 笔明细"
            "（intent→receipt→onchain）。用于自查与向用户汇报每笔开销；也是与平台流水、"
            "链上 Charged 事件对账的出口。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recent": {
                    "type": "integer",
                    "description": "返回最近 N 条账本明细（默认 10）",
                    "default": 10,
                }
            },
            "required": [],
        },
    },
    {
        "name": "wallet_status",
        "description": (
            "钱包健康自查（免费；挂载后第一件事）：地址/链/USDT 余额/对 PayVault 授权额度/"
            "L0 引擎各限额现值。余额或授权为 0 时返回 hint_fund_wallet / hint_approve_vault"
            " 人话指引——把 hint 转告用户去补，不要自行重试付费调用。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
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


# ---- 新三只读工具的载荷构造（决策闭环：看价 / 自查 / 汇报；实现参考 agent_loop.py） ----

#: advice 通道短超时（consumer-agent-interface advice 通道）：core 慢不拖报价本体
ADVICE_TIMEOUT_S = 3.0


def _advice_view(
    client: Client,
    service_id: str,
    manifest: dict[str, Any],
    budget: dict[str, Any] | None,
) -> dict[str, Any]:
    """core /advice 判定式投影嵌入报价（Agent 默认介入通道）。

    失败一律降级 {"error": "unavailable"}——advice 是增强不是依赖，绝不拖垮报价。
    category 口径与 core 读取侧一致（manifest 缺省 other）；daily 透传 L0 日额现值。
    """
    category = manifest.get("category")
    daily = budget.get("daily_budget_raw") if budget is not None else None
    try:
        return client.advice(
            category=str(category) if category else "other",
            current=service_id,
            daily_budget_raw=daily if isinstance(daily, int) and daily > 0 else None,
            timeout_s=ADVICE_TIMEOUT_S,
        )
    except (CoinCallError, httpx.HTTPError, ValueError):
        return {"error": "unavailable"}


def _wallet_view(client: Client) -> tuple[dict[str, Any] | None, str | None]:
    """余额/授权视图；链查询失败不致命（None + 人话 note，看价仍可用）。"""
    if client.wallet is None:
        return None, None
    try:
        bal = client.wallet.balance()
    except CoinCallError as exc:
        return None, f"链上余额查询失败：{exc}"
    return {
        "usdt_balance_raw": bal.usdt_balance_raw,
        "vault_allowance_raw": bal.vault_allowance_raw,
        "available_raw": bal.available_raw,
    }, None


def _wallet_hints(
    client: Client,
    wallet: dict[str, Any] | None,
    note: str | None,
    price_raw: int,
    amount: str,
) -> list[str]:
    """看价时的钱包侧人话提示（缺钱包/链断/余额不足/授权不足）。"""
    hints: list[str] = []
    if wallet is None:
        if client.wallet is None:
            hints.append("未装配付费钱包：设置 COINCALL_WALLET_KEY（0x 私钥或 0600 key 文件路径）")
        if note:
            hints.append(note)
        return hints
    if wallet["usdt_balance_raw"] < price_raw:
        hints.append(f"余额不足：本笔 {price_raw} raw；测试网可用 wallet.mint() 充值 MockUSDT")
    if wallet["vault_allowance_raw"] < price_raw:
        hints.append(f"授权不足：执行 wallet.approve_vault('{amount}') 向 PayVault 授权")
    return hints


def _budget_hints(budget: dict[str, Any], price_raw: int) -> list[str]:
    """看价时的 L0 预算侧人话提示（总额/日额余量不足）。"""
    hints: list[str] = []
    total_left, daily_left = budget.get("total_left_raw"), budget.get("daily_left_raw")
    if total_left is not None and total_left < price_raw:
        hints.append(f"总额预算余量 {total_left} 不足以支付本笔 {price_raw}")
    if daily_left is not None and daily_left < price_raw:
        hints.append(f"日额预算余量 {daily_left} 不足以支付本笔 {price_raw}")
    return hints


def service_quote_payload(client: Client, service_id: str) -> dict[str, Any]:
    """单服务报价：目录定价 + 收款方 PayVault + 钱包余额/授权 + L0 预算余量。"""
    match: dict[str, Any] | None = None
    for svc in client.catalog().get("services", []):
        if svc.get("service_id") == service_id:
            match = svc
            break
    if match is None:
        raise CoinCallError(f"服务不在目录: {service_id}（先 catalog 查可用服务与定价）")
    manifest = match.get("manifest", {})
    pricing = manifest.get("pricing", {})
    price_raw = int(pricing.get("amount_raw", 0))
    amount = str(pricing.get("amount", price_raw))
    payee = client.wallet.pay_vault if client.wallet is not None else PAY_VAULT_ADDRESS

    wallet, wallet_note = _wallet_view(client)
    budget = client.policy.summary() if client.policy is not None else None
    hints = _wallet_hints(client, wallet, wallet_note, price_raw, amount)
    affordable_by_wallet = None if wallet is None else wallet["available_raw"] >= price_raw
    affordable_by_budget: bool | None = None
    if budget is not None:
        hints.extend(_budget_hints(budget, price_raw))
        limits = [
            left
            for left in (budget.get("total_left_raw"), budget.get("daily_left_raw"))
            if left is not None
        ]
        if limits:
            affordable_by_budget = min(limits) >= price_raw

    return {
        "service_id": service_id,
        "name": manifest.get("name"),
        "pricing": pricing,
        "price_raw": price_raw,
        "payee": payee,
        "wallet": wallet,
        "affordable_by_wallet": affordable_by_wallet,
        "budget": budget,
        "affordable_by_budget": affordable_by_budget,
        "advice": _advice_view(client, service_id, manifest, budget),
        "hints": hints,
    }


def wallet_status_payload(client: Client) -> dict[str, Any]:
    """钱包健康：地址/链/余额/授权/L0 现值；缺什么给 hint_* 人话（水龙头/approve）。"""
    payload: dict[str, Any] = {
        "address": None,
        "chain_id": None,
        "token_address": None,
        "pay_vault": PAY_VAULT_ADDRESS,
        "usdt_balance_raw": None,
        "vault_allowance_raw": None,
        "available_raw": None,
        "api_key_present": bool(client.api_key),
        "policy": client.policy.summary() if client.policy is not None else None,
    }
    if client.wallet is None:
        payload["hint_no_wallet"] = (
            "未装配付费钱包：设置 COINCALL_WALLET_KEY（0x 私钥或 0600 key 文件路径）——"
            "没有钱包只能浏览目录，无法付费调用"
        )
        return payload
    wallet_obj = client.wallet
    payload["address"] = wallet_obj.address
    payload["chain_id"] = wallet_obj.chain_id
    payload["token_address"] = wallet_obj.token_address
    payload["pay_vault"] = wallet_obj.pay_vault
    wallet, note = _wallet_view(client)
    if wallet is None:
        payload["hint_chain_unreachable"] = note or "链上余额查询失败（稍后重试 wallet_status）"
        return payload
    payload.update(wallet)
    if wallet["usdt_balance_raw"] == 0:
        payload["hint_fund_wallet"] = (
            "钱包 USDT 余额为 0：测试网用 wallet.mint('10')（MockUSDT 公开水龙头）充值；"
            "主网向该地址转入 USDT"
        )
    if wallet["vault_allowance_raw"] == 0:
        payload["hint_approve_vault"] = (
            "对 PayVault 授权额度为 0：执行 wallet.approve_vault('5') 授权后才能付费扣款"
        )
    return payload


def spend_report_payload(client: Client, recent: int = 10) -> dict[str, Any]:
    """本地账本聚合：L0 余量/已花 + 最近 N 笔明细（最新在前）。"""
    try:
        n = max(1, min(int(recent), 100))
    except (TypeError, ValueError):
        n = 10
    if client.policy is None:
        return {
            "policy": None,
            "hint_no_policy": (
                "L0 策略引擎未启用：设置 COINCALL_TOTAL/DAILY/PER_CALL_BUDGET_RAW"
                "（或旧 COINCALL_BUDGET_RAW）后，所有付费调用自动经本地预算闸与审计账本"
            ),
            "spent_in_session_raw": client.spent_raw,
            "recent": [],
        }
    return {
        "policy": client.policy.summary(),
        "spent_in_session_raw": client.spent_raw,
        "recent": client.policy.ledger.recent(n),
    }


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
                if tool_name not in TOOL_NAMES:
                    if req_id is None:
                        return None
                    return self._error(
                        req_id,
                        -32602,
                        f"unknown tool: {tool_name}（可用: {' / '.join(TOOL_NAMES_ORDERED)}）",
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

    def _dispatch_tool(self, name: str, arguments: dict[str, Any]) -> _ToolOutcome:  # noqa: PLR0911 —— 工具分发器天然多分支
        """五工具分发；CoinCallError 一律转 isError 文本（含 402 人话指引）。"""
        try:
            if name == "catalog":
                return _ToolOutcome(
                    json.dumps(self.client().catalog(), ensure_ascii=False, default=str)
                )
            if name == "service_quote":
                service_id = str(arguments.get("service_id", ""))
                if not service_id:
                    return _ToolOutcome("参数错误：需要 service_id (str)", is_error=True)
                return _ToolOutcome(
                    json.dumps(
                        service_quote_payload(self.client(), service_id),
                        ensure_ascii=False,
                        default=str,
                    )
                )
            if name == "wallet_status":
                return _ToolOutcome(
                    json.dumps(
                        wallet_status_payload(self.client()), ensure_ascii=False, default=str
                    )
                )
            if name == "spend_report":
                return _ToolOutcome(
                    json.dumps(
                        spend_report_payload(self.client(), arguments.get("recent", 10)),
                        ensure_ascii=False,
                        default=str,
                    )
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
                f"未知工具: {name}（可用: {' / '.join(TOOL_NAMES_ORDERED)}）", is_error=True
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
