#!/usr/bin/env python3
"""Consumer Agent CLI —— 真实 LLM（DashScope，openai 兼容）经 CoinCall MCP 完成付费任务。

全流程真实（无 fake/脚本路径）：LLM 意图识别 → MCP 工具发现（tools/list）→
function-calling 工具循环（catalog → service_quote 平台建议 → paid_service_call 签名付费）
→ 基于真实返回数据回答。机械闸门（不依赖 LLM 自觉）：paid_service_call 前必须先对同一
service_id 调 service_quote（获取报价 + advice 建议）；provider 失败 → 网关不结算，不扣款。

运行（在 coincall-sdk 的环境里跑，MCP 子进程复用同一解释器）：
  uv run --project ../coincall-sdk python consumer_agent.py "帮我查询 binance 期货中目前涨得最猛的交易对有哪些？"

配置（coincall-examples/.env，真实环境变量优先）：
  DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL / DASHSCOPE_MODEL   LLM 三件套
  COINCALL_WALLET_KEY     消费热钱包：0x 私钥或 0600 key 文件路径（不要放主钱包私钥）
  COINCALL_API_KEY        平台 api key（缺省时自动向 core 签发并落 0600 文件）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

EXAMPLES_DIR = Path(__file__).resolve().parent
SDK_DIR = EXAMPLES_DIR.parent / "coincall-sdk"
ENV_FILE = EXAMPLES_DIR / ".env"
KEY_FILE = Path.home() / ".coincall" / "apikey"  # 0600，明文只进这里
CORE_URL = "http://127.0.0.1:8020"

LINE = "━" * 62


# ============================================================
# 配置装配
# ============================================================

def load_dotenv(path: Path) -> None:
    """极简 .env：KEY=VALUE 逐行；已存在的真实环境变量优先（setdefault）。"""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def wallet_source_label() -> str:
    """钱包来源标注：路径原样 / 内联私钥只说"内联"（不回显任何私钥片段）+ 派生公开地址（完整）。"""
    src = os.environ.get("COINCALL_WALLET_KEY", "").strip()
    if not src:
        return "未配置——只能浏览，无法付费（设置 COINCALL_WALLET_KEY=0x私钥 或 0600 文件路径）"
    kind = src if os.path.isfile(src) else "内联 0x 私钥（不回显）"
    try:
        sys.path.insert(0, str(SDK_DIR))
        from coincall.wallet import LocalWallet

        return f"{kind} → 地址 {LocalWallet.from_key(src).address}"
    except Exception as exc:  # noqa: BLE001 —— 标注失败要给出人话而非崩溃
        return f"{kind}（⚠ 地址解析失败：{exc}——检查是否合法 0x+64hex 私钥 / 0600 文件）"


def bootstrap_api_key() -> str:
    """平台 api key：env → 0600 文件 → 向 core 签发（请求只带公开地址，不带任何秘密）。

    结果写回 os.environ——MCP 子进程继承环境变量，必须在此赋值。
    """
    if os.environ.get("COINCALL_API_KEY"):
        return os.environ["COINCALL_API_KEY"]
    api_key = ""
    if KEY_FILE.exists():
        api_key = KEY_FILE.read_text().strip()
    if not api_key:
        sys.path.insert(0, str(SDK_DIR))
        from coincall.wallet import LocalWallet  # noqa: E402 —— 仅取公开地址做绑定

        wallet = LocalWallet.from_key(os.environ.get("COINCALL_WALLET_KEY", ""))
        resp = httpx.post(
            f"{CORE_URL}/apikeys", json={"consumer_wallet": wallet.address}, timeout=10,
            trust_env=False,
        )
        resp.raise_for_status()
        api_key = resp.json()["api_key"]
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(api_key)
        KEY_FILE.chmod(0o600)
        print(f"[0] api key 已签发并落盘 {KEY_FILE}（0600）")
    os.environ["COINCALL_API_KEY"] = api_key
    return api_key


# ============================================================
# MCP stdio 客户端（子进程：同解释器 -m coincall.mcp，失败回退 uv 入口）
# ============================================================

class McpClient:
    """极简 MCP stdio 客户端：单线程严格请求-响应（一行一帧 JSON-RPC 2.0）。"""

    def __init__(self) -> None:
        env = dict(os.environ)
        env["NO_PROXY"] = "*"  # 网关/core 都是本机地址，防系统代理劫持 localhost
        env["no_proxy"] = "*"
        self._stderr = tempfile.TemporaryFile("w+")  # 防 stderr 管道积压阻塞子进程
        self._id = 0
        cmds = [
            [sys.executable, "-m", "coincall.mcp"],
            ["uv", "run", "--project", str(SDK_DIR), "coincall-mcp"],
        ]
        last_err = ""
        for cmd in cmds:
            try:
                self.proc = subprocess.Popen(  # type: ignore[attr-defined]
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=self._stderr, env=env, text=True, bufsize=1,
                )
                self.request("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {}, "clientInfo": {"name": "consumer-agent-cli", "version": "0.1.0"},
                })
                return
            except (RuntimeError, OSError) as exc:
                last_err = f"{cmd[0]}: {exc}"
        raise RuntimeError(f"MCP 子进程无法启动（{last_err}）——请在 coincall-sdk 环境里运行本 CLI")

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        rid = self._id
        frame: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            frame["params"] = params
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                self._stderr.seek(0)
                tail = self._stderr.read()[-600:]
                raise RuntimeError(f"MCP 子进程退出：{tail}")
            msg = json.loads(line)
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"MCP 错误：{msg['error']}")
                return msg["result"]

    def tools_list(self) -> list[dict[str, Any]]:
        return self.request("tools/list")["tools"]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """返回 (text, is_error)——错误也是结构化文本（人话指引），不抛异常。"""
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        text = ""
        content = result.get("content") or [{}]
        if content and isinstance(content[0], dict):
            text = str(content[0].get("text", ""))
        return text, bool(result.get("isError"))

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        finally:
            self._stderr.close()


# ============================================================
# LLM（DashScope openai 兼容；外部地址直连失败自动走系统代理重试）
# ============================================================

class Llm:
    def __init__(self) -> None:
        self.api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        self.base_url = os.environ.get("DASHSCOPE_BASE_URL", "").rstrip("/")
        self.model = os.environ.get("DASHSCOPE_MODEL", "")
        if not (self.api_key and self.base_url and self.model):
            raise SystemExit(
                "[!] .env 缺少 DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL / DASHSCOPE_MODEL —— "
                "本 CLI 只跑真实 LLM 流程，不提供脚本模式"
            )

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             temperature: float = 0.2) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=120, trust_env=False)
        except httpx.HTTPError:
            resp = httpx.post(url, json=payload, headers=headers, timeout=120, trust_env=True)
        if resp.status_code >= 400:
            raise SystemExit(f"[!] LLM 调用失败 {resp.status_code}：{resp.text[:300]}")
        return resp.json()["choices"][0]["message"]


# ============================================================
# 流程输出：每步一行人话摘要（目录/报价/付费/自查各取关键字段）
# ============================================================

def _usdt(raw: Any) -> str:
    try:
        return f"{int(raw) / 1e6:g}"
    except (TypeError, ValueError):
        return "?"


def digest(name: str, text: str, is_error: bool) -> str:
    """工具结果 → 一行摘要；付费失败单独提示「未扣款」。"""
    if is_error:
        if name == "paid_service_call":
            return (f"❌ 调用失败 → 未扣款（平台只在 provider 成功响应后才结算，本次无收据、"
                    f"不会上链 Charged）。指引：{text[:180]}")
        return f"⚠ {text[:200]}"
    try:
        d = json.loads(text)
    except (ValueError, TypeError):
        return text[:200]
    if name == "catalog":
        svcs = d.get("services", [])
        items = "；".join(
            f"{s['service_id']}（{s['manifest']['name']} · {s['manifest'].get('category', '-')} · "
            f"{s['manifest']['pricing']['amount']} USDT/次）"
            for s in svcs[:5]
        )
        return f"{len(svcs)} 个服务：{items}"
    if name == "service_quote":
        advice = d.get("advice") or {}
        verb = advice.get("verb") or advice.get("error") or "-"
        hints = d.get("hints") or []
        return (f"{d.get('name')} · {d['pricing']['amount']} USDT/次 ｜ 余额可付={d.get('affordable_by_wallet')}"
                f" 预算可付={d.get('affordable_by_budget')} ｜ 平台建议 advice={verb}"
                + (f"（{advice.get('reason', '')[:70]}）" if advice.get("reason") else "")
                + (f" ｜ ⚠ hints：{'；'.join(hints[:2])}" if hints else ""))
    if name == "paid_service_call":
        receipt = str(d.get("receipt_id") or "-")
        return (f"✅ status={d.get('status_code')} ｜ 扣款 {_usdt(d.get('charged_raw'))} USDT"
                f" ｜ 收据 {receipt}"
                f"（Ed25519 回执签名已验）")
    if name == "wallet_status":
        return (f"地址 {d.get('address')} ｜ USDT 余额 {_usdt(d.get('usdt_balance_raw'))}"
                f" ｜ PayVault 授权 {_usdt(d.get('vault_allowance_raw'))}")
    if name == "spend_report":
        policy = d.get("policy") or {}
        return f"本次会话已花 {_usdt(d.get('spent_in_session_raw'))} USDT ｜ L0 余量：{policy.get('summary', '') or policy}"
    return text[:200]


# ============================================================
# Agent：意图识别 → 工具循环（含机械报价闸门）
# ============================================================

SYSTEM_PROMPT = """你是接入「琢信 CoinCall」平台的 Consumer Agent，通过 MCP 五个工具\
（wallet_status / catalog / service_quote / paid_service_call / spend_report）替用户完成付费数据任务。

工作纪律：
1. 流程固定：catalog 发现服务（按用户需求匹配 name/description/tags）→ service_quote 看价与平台建议 →
   paid_service_call 付费调用 → 基于返回数据回答用户问题。
2. 付费前必须先 service_quote 同一服务：hints 非空（余额/授权/预算不足）→ 不要调用，把提示转告用户；
   advice.verb=switch → 按 alternative 换推荐服务并向用户说明理由；proceed/keep/insufficient_data → 可继续。
3. paid_service_call 是唯一花钱动作：params 严格按该服务 input_schema 构造（catalog 里有）；
   isError=true 时绝不重试付费调用，把人话指引转告用户（失败不会扣款）。
4. 账本语义（重要，勿误读）：spend_report 的 policy 数字是**跨会话持久账本**，含此前所有会话的
   历史支出与旧收据；本次会话的真实支出只看 spent_in_session_raw。付费调用失败（402/5xx/超时）
   **不会写入任何账本记录**——不要把历史收据归因于本次失败的调用。
5. 回答必须基于工具返回的真实数据并引用关键数字，禁止编造；结尾附一行报账：调用了什么服务、
   花费多少 USDT、收据号（没有付费调用就说明没有产生费用）。
全程使用中文。"""

MAX_TURNS = 12


class Agent:
    def __init__(self, mcp: McpClient, llm: Llm) -> None:
        self.mcp = mcp
        self.llm = llm
        self.quoted: set[str] = set()          # 报价闸门：已 service_quote 的服务
        self.n_tool_calls = 0
        self.n_paid = 0
        self.spend_raw = 0
        self.receipts: list[str] = []
        self.n_failed_paid = 0

    def exec_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """执行一个 MCP 工具；付费调用前强制同服务已报价（机械闸门，防 LLM 跳步）。"""
        if name == "paid_service_call":
            sid = str(arguments.get("service_id", ""))
            if sid and sid not in self.quoted:
                return (f"流程闸门：付费前必须先调用 service_quote(service_id=\"{sid}\") "
                        f"获取报价与平台建议（advice/hints），确认后再付费调用。", True)
        text, is_error = self.mcp.call_tool(name, arguments)
        self.n_tool_calls += 1
        if name == "service_quote" and not is_error and arguments.get("service_id"):
            self.quoted.add(str(arguments["service_id"]))
        if name == "paid_service_call":
            if is_error:
                self.n_failed_paid += 1
            else:
                self.n_paid += 1
                try:
                    self.spend_raw += int(json.loads(text).get("charged_raw") or 0)
                    self.receipts.append(str(json.loads(text).get("receipt_id") or "-"))
                except (ValueError, TypeError):
                    pass
        return text, is_error

    def run(self, user_prompt: str, tools_spec: list[dict[str, Any]]) -> str:
        # —— 阶段 1：意图识别（不带工具；让 LLM 先说清要什么、该找哪类服务）——
        print("[2] 意图识别（LLM）…")
        intent = self.llm.chat([
            {"role": "system", "content":
                "分析用户请求，用两三句中文说清：需要什么数据/能力、判断依据、大概匹配什么类型的服务"
                "（行情/翻译/链上查询等）。只输出意图说明本身。"},
            {"role": "user", "content": user_prompt},
        ])["content"] or ""
        print(f"    → {str(intent).strip()[:220]}")

        # —— 阶段 2：工具循环（function calling）——
        openai_tools = [
            {"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
            }}
            for t in tools_spec
        ]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        print("    ── 进入工具循环（LLM function calling → MCP）──")
        for turn in range(1, MAX_TURNS + 1):
            msg = self.llm.chat(messages, tools=openai_tools)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                print(f"[{turn + 2}] 🧾 最终回答（LLM 综合工具结果）：")
                return str(msg.get("content") or "").strip()
            messages.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })
            for tc in tool_calls:
                fn = tc["function"]
                name = fn["name"]
                try:
                    arguments = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    arguments = {"_raw": fn.get("arguments")}
                print(f"[{turn + 2}] 🔧 {name}({json.dumps(arguments, ensure_ascii=False)[:160]})")
                text, is_error = self.exec_tool(name, arguments)
                print(f"    → {digest(name, text, is_error)}")
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"], "name": name, "content": text,
                })
        return "（达到最大工具轮数上限，任务未完成——请检查服务状态或收紧问题）"


# ============================================================
# main
# ============================================================

def main() -> None:
    load_dotenv(ENV_FILE)
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        raise SystemExit("用法：python consumer_agent.py \"你的问题\"（例：帮我查询 binance 期货中目前涨得最猛的交易对有哪些？）")

    llm = Llm()
    bootstrap_api_key()

    print(LINE)
    print("琢信 CoinCall · Consumer Agent CLI（全真实流程：LLM + MCP + 链上付费）")
    print(LINE)
    host = llm.base_url.split("//")[-1].split("/")[0]
    print(f"[0] 装配：LLM={llm.model} @ {host} ｜ 钱包={wallet_source_label()}")

    mcp = McpClient()
    agent = Agent(mcp, llm)
    try:
        # 钱包自查（机械步骤：装配即验，余额/授权/预算一眼可见）
        text, is_error = mcp.call_tool("wallet_status", {})
        print(f"[1] 钱包自查 wallet_status → {digest('wallet_status', text, is_error)}")

        tools = mcp.tools_list()
        print(f"    MCP 工具发现：{len(tools)} 个 —— {' / '.join(t['name'] for t in tools)}")

        answer = agent.run(prompt, tools)

        print()
        print(answer)
        print(LINE)
        print("流程摘要")
        print(f"  工具调用 {agent.n_tool_calls + 1} 次（含自查）｜ 成功付费 {agent.n_paid} 次 ｜ "
              f"支出 {_usdt(agent.spend_raw)} USDT ｜ 收据 {len(agent.receipts)} 张 ｜ "
              f"失败 {agent.n_failed_paid} 次（失败不扣款）")
        if agent.receipts:
            print(f"  收据号：{', '.join(agent.receipts)}")
        print(LINE)
    finally:
        mcp.close()


if __name__ == "__main__":
    main()
