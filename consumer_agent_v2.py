#!/usr/bin/env python3
"""Consumer Agent v2 —— 职责分层后的宿主侧示例（对照 consumer_agent.py v1）。

分层原则：纪律性约束尽量靠近平台（网关 > MCP > skill 提示词 > 宿主自觉），
表达与编排留给宿主。因此本文件**只做宿主该做的三件事**：

  ① 挂 MCP        —— 平台 SDK 的 McpSession（stdio 协议细节在平台层）
  ② LLM 编排      —— 意图识别 → function calling 工具循环 → 综合回答
  ③ 引用 skill    —— 工作纪律唯一事实源 = skills/coincall-consumer/SKILL.md

以下能力都在平台层，宿主零代码（v1 里这些被硬编码在 CLI，共 ~250 行）：
  - 报价闸门（paid 前必须 quote）      → coincall-mcp 进程内置
  - 工具结果人话摘要                    → MCP 响应 summary 字段（summary_of 直接转述）
  - api key 引导签发（0600 落盘）       → SDK ensure_api_key()
  - 消费纪律系统提示                    → SKILL.md（本文件运行时读取）

运行：
  uv run --project ../coincall-sdk python consumer_agent_v2.py "帮我查询 binance 期货中目前涨得最猛的交易对有哪些？"
配置：coincall-examples/.env（DASHSCOPE_* 三件套 + COINCALL_WALLET_KEY 等）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

EXAMPLES_DIR = Path(__file__).resolve().parent
SDK_DIR = EXAMPLES_DIR.parent / "coincall-sdk"
SKILL_FILE = SDK_DIR / "skills" / "coincall-consumer" / "SKILL.md"
ENV_FILE = EXAMPLES_DIR / ".env"
sys.path.insert(0, str(SDK_DIR))

from coincall.client import ensure_api_key  # noqa: E402 —— 平台层：接入引导
from coincall.mcp_client import McpSession  # noqa: E402 —— 平台层：MCP stdio 会话
from coincall.wallet import LocalWallet  # noqa: E402

MAX_TURNS = 12
LINE = "━" * 62


# ============ 宿主侧：配置装配（.env 是宿主自己的配置来源选择） ============

def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


# ============ 宿主侧：LLM（openai 兼容；这是宿主自己的模型与编排） ============

class Llm:
    def __init__(self) -> None:
        self.api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        self.base_url = os.environ.get("DASHSCOPE_BASE_URL", "").rstrip("/")
        self.model = os.environ.get("DASHSCOPE_MODEL", "")
        if not (self.api_key and self.base_url and self.model):
            raise SystemExit("[!] .env 缺少 DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL / DASHSCOPE_MODEL")

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0.2}
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


# ============ 宿主侧：纪律唯一事实源 = SKILL.md（缺省兜底并告警） ============

def system_prompt() -> str:
    if SKILL_FILE.exists():
        skill = SKILL_FILE.read_text()
        return (
            "你是接入「琢信 CoinCall」平台的 Consumer Agent。以下是平台消费纪律"
            "（coincall-consumer skill，唯一事实源），严格遵守：\n\n"
            f"{skill}\n\n"
            "补充（宿主运行时约定）：回答用中文；基于工具返回的真实数据并引用关键数字；"
            "工具响应的 summary 字段是人话摘要，可直接转述给用户。"
        )
    print("[!] 未找到 SKILL.md——使用内置兜底纪律（生产环境不应出现）")
    return (
        "你是 CoinCall Consumer Agent。流程：wallet_status 自查 → catalog 找服务 → "
        "service_quote 看价与平台建议 → paid_service_call 付费 → spend_report 汇报。"
        "付费失败绝不重试（不扣款），把人话指引转告用户；回答基于工具真实数据，中文。"
    )


# ============ 宿主侧：编排（意图识别 + 工具循环） ============

def run(mcp: McpSession, llm: Llm, user_prompt: str, tools_spec: list[dict[str, Any]]) -> str:
    intent = llm.chat([
        {"role": "system", "content": "用两三句中文说清：用户需要什么数据/能力、大概匹配什么类型的服务。只输出意图说明。"},
        {"role": "user", "content": user_prompt},
    ])["content"] or ""
    print(f"[2] 意图识别（LLM）→ {str(intent).strip()[:200]}")
    print("    ── 工具循环（function calling → MCP；闸门/摘要都在平台层）──")

    openai_tools = [
        {"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
        }}
        for t in tools_spec
    ]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": user_prompt},
    ]
    for turn in range(1, MAX_TURNS + 1):
        msg = llm.chat(messages, tools=openai_tools)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            print(f"[{turn + 2}] 🧾 最终回答（LLM 综合工具结果）：")
            return str(msg.get("content") or "").strip()
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc["function"]
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                arguments = {"_raw": fn.get("arguments")}
            print(f"[{turn + 2}] 🔧 {fn['name']}({json.dumps(arguments, ensure_ascii=False)[:120]})")
            text, _is_error = mcp.call_tool(fn["name"], arguments)
            print("    → " + McpSession.summary_of(text).replace("\n", "\n    "))
            messages.append({"role": "tool", "tool_call_id": tc["id"], "name": fn["name"], "content": text})
    return "（达到最大工具轮数，任务未完成）"


def main() -> None:
    load_dotenv(ENV_FILE)
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        raise SystemExit('用法：python consumer_agent_v2.py "你的问题"')

    llm = Llm()
    wallet = LocalWallet.from_key(os.environ.get("COINCALL_WALLET_KEY", ""))
    ensure_api_key(wallet)  # 平台层引导：env → 0600 文件 → core 签发（明文只落盘一次）

    print(LINE)
    print("琢信 CoinCall · Consumer Agent v2（分层宿主：挂 MCP + 编排 + skill 纪律）")
    print(LINE)
    print(f"[0] 装配：LLM={llm.model} ｜ 钱包 {wallet.address} ｜ 纪律源 SKILL.md={'✓' if SKILL_FILE.exists() else '✗'}")

    with McpSession() as mcp:  # 平台层：stdio 会话（子进程=coincall-mcp）
        text, _ = mcp.call_tool("wallet_status", {})
        print(f"[1] 钱包自查 → {McpSession.summary_of(text)}")
        tools = mcp.tools_list()
        print(f"    MCP 工具发现：{len(tools)} 个 —— {' / '.join(t['name'] for t in tools)}")
        answer = run(mcp, llm, prompt, tools)
        print()
        print(answer)
        print(LINE)


if __name__ == "__main__":
    main()
