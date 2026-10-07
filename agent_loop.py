#!/usr/bin/env python3
"""最小 Agent Loop 示例：Tools + Skill 双集成，经 CoinCall 完成付费调用。

展示三件事（consumer-agent-interface.md 的可运行印证）：
1. 最简 Agent Loop（while + LLM + tool dispatch），无框架依赖；
2. 引导式接入（bootstrap）：本地钱包私钥 → 链上自验 → 平台签发 api key（明文只落 0600 文件）；
3. 两种消费路径：Tools 直调 SDK / Skill 纪律包裹（先 quote 后 call、超限问人、失败不重试）。

运行（测试网，anvil#1 公开测试钥仅演示）：
  uv run --project ../coincall-sdk python agent_loop.py
环境（缺一走引导）：
  COINCALL_WALLET_KEY=0x59c6995e…  或指向 0600 key 文件路径
  COINCALL_API_KEY=（留空则本示例经 core 签发并写入 ~/.coincall/apikey）
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "coincall-sdk"))

from coincall.client import Client  # noqa: E402
from coincall.policy import PolicyConfig, PolicyEngine  # noqa: E402
from coincall.wallet import LocalWallet  # noqa: E402

CORE = "http://127.0.0.1:8020"
GATEWAY = "http://127.0.0.1:8030"
KEY_FILE = Path.home() / ".coincall" / "apikey"  # 0600，明文只进这里


# ============================================================
# 第 2 步：引导式接入（安全获取平台 api key）
# ============================================================

def bootstrap() -> tuple[LocalWallet, str]:
    """本地私钥 → 钱包 → core 签发绑定该钱包的 api key（明文只落 0600 文件）。

    安全要点：
    - 私钥只从 env/文件进内存，永不打印、永不出现在任何网络请求体；
    - api key 向 core 请求时只带 consumer_wallet（公开地址），不带任何秘密；
    - key 明文落盘 0600 一次，之后进程只从文件读——等价控制台"明文只回显一次"。
    """
    wallet = LocalWallet.from_key(os.environ.get("COINCALL_WALLET_KEY", ""))

    if os.environ.get("COINCALL_API_KEY"):
        return wallet, os.environ["COINCALL_API_KEY"]
    if KEY_FILE.exists():
        return wallet, KEY_FILE.read_text().strip()

    import httpx

    resp = httpx.post(
        f"{CORE}/apikeys", json={"consumer_wallet": wallet.address}, timeout=10
    )
    resp.raise_for_status()
    api_key: str = resp.json()["api_key"]
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(api_key)
    KEY_FILE.chmod(0o600)
    print(f"[bootstrap] 钱包 {wallet.address} 已绑定，api key 落盘 {KEY_FILE}（0600）")
    return wallet, api_key


# ============================================================
# 第 3 步-A：Tools 面（SDK 直调，等价 MCP 的五个工具）
# ============================================================

def build_tools(client: Client) -> dict[str, Any]:
    """Agent 可调用的工具注册表——与 coincall-mcp 的 5 工具一一对应。"""

    def wallet_status() -> str:
        bal = client.wallet.balance() if client.wallet else None
        p = client.policy
        return json.dumps({
            "address": client.wallet.address if client.wallet else None,
            "usdt_balance_raw": str(bal.usdt_balance_raw) if bal else "0",
            "vault_allowance_raw": str(bal.vault_allowance_raw) if bal else "0",
            "policy": {
                "total_left": (p.cfg.total_budget_raw - p.state.total_spent_raw) if p and p.cfg.total_budget_raw else None,
                "daily_left": (p.cfg.daily_budget_raw - p.state.daily_spent_raw) if p and p.cfg.daily_budget_raw else None,
                "disabled": bool(p and p.state.disabled_reason),
            },
        }, ensure_ascii=False)

    def catalog() -> str:
        svcs = client.catalog().get("services", [])
        return json.dumps([
            {"service_id": s["service_id"], "price": s["manifest"]["pricing"]["amount"]}
            for s in svcs
        ], ensure_ascii=False)

    def service_quote(service_id: str) -> str:
        svcs = {s["service_id"]: s for s in client.catalog().get("services", [])}
        s = svcs.get(service_id)
        if not s:
            return json.dumps({"error": "not_found"}, ensure_ascii=False)
        price_raw = int(s["manifest"]["pricing"]["amount_raw"])
        p = client.policy
        return json.dumps({
            "service_id": service_id, "price_raw": price_raw,
            "daily_left_raw": (p.cfg.daily_budget_raw - p.state.daily_spent_raw) if p and p.cfg.daily_budget_raw else None,
        }, ensure_ascii=False)

    def paid_service_call(service_id: str, params: dict) -> str:
        r = client.call(service_id, params)  # L0 在签名前拦截
        return json.dumps({
            "body": r.body, "receipt_id": r.receipt_id, "charged_raw": r.charged_raw,
        }, ensure_ascii=False, default=str)

    return {
        "wallet_status": wallet_status,
        "catalog": catalog,
        "service_quote": service_quote,
        "paid_service_call": paid_service_call,
    }


# ============================================================
# 第 3 步-B：Skill 纪律（包裹 Tools 的操作契约）
# ============================================================

SKILL_CONTRACT = """
你是 Consumer Agent。使用 CoinCall 付费服务时遵守：
1. 首次调用前 wallet_status 自查；缺余额/授权 → 停下报告用户，不重试；
2. 先 catalog 找服务，再 service_quote 看价；价格超日额 10% 或服务不在白名单 → 询问用户；
3. paid_service_call 失败 → 把错误人话转告用户，绝不自动重试付费调用；
4. 每笔付费调用完成后，用一句话报出：买了什么/花了多少/收据号。
"""


# ============================================================
# 第 1 步：最小 Agent Loop（无框架；真实场景把 mock_llm 换成你的 LLM）
# ============================================================

def mock_llm(task: str, tools: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """替代真实 LLM 的规划器：按 skill 纪律产出工具调用序列。"""
    return [
        ("wallet_status", {}),
        ("catalog", {}),
        ("service_quote", {"service_id": "binance_future_ai_increase_top_n"}),
        ("paid_service_call", {"service_id": "binance_future_ai_increase_top_n", "params": {"query": "BTC 价格"}}),
    ]


def agent_loop(task: str) -> None:
    wallet, api_key = bootstrap()
    client = Client(
        api_key=api_key,
        wallet=wallet,
        gateway_url=GATEWAY,
        core_url=CORE,
        policy=PolicyConfig(  # L0：三重预算+白名单（引导配好后 Agent 永不越界）
            total_budget_raw=1_000_000, daily_budget_raw=100_000,
            max_per_call_raw=10_000, allowed_service_ids=["binance_future_ai_increase_top_n"],
        ),
    )
    tools = build_tools(client)
    print(f"[agent] 任务: {task}\n[agent] skill 纪律已装载")

    for step, (name, args) in enumerate(mock_llm(task, tools), 1):
        print(f"\n--- step {step}: {name}({json.dumps(args, ensure_ascii=False)})")
        result = tools[name](**args)
        print(result[:300])
        if name == "paid_service_call":
            r = json.loads(result)
            print(f"[agent] 汇报: 调用 {args['service_id']} 成功，花费 "
                  f"{int(r['charged_raw']) / 1e6} USDT，收据 {r['receipt_id']}")


if __name__ == "__main__":
    agent_loop("查一下 RadAI 报的 BTC 价格")
