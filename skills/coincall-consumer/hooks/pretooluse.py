#!/usr/bin/env python3
"""CoinCall PreToolUse hook 样例：paid_service_call 前自动注入 /advice 建议。

宿主（Claude Code / ZCode）在每次工具调用前把 toolcall payload 从 stdin 递给本脚本；
本脚本只关心 paid_service_call——先问 core 的决策引擎（GET /advice?current=…），
把"verb + reason 人话"作为 additionalContext 注回对话，Agent 听劝后再决定。

行为契约（安全优先）：
  - 非 paid_service_call 工具：静默放行（空 stdout，exit 0）；
  - 默认模式：只注入 additionalContext，**绝不 deny、也绝不 auto-allow**
    （allow 会绕过宿主的人审确认——付费工具永不由 hook 放行）；
  - COINCALL_ENFORCE_ADVICE=1（强制听劝）：verb=switch（有更优服务）或
    verb=insufficient_data（分区无数据，盲付高风险）→ permissionDecision=deny；
  - 任何失败（core 不可达/stdin 非法/缺 httpx）：fail-open 注入 unavailable 提示，
    exit 0——advice 是增强不是依赖，hook 永不成为单点故障。

环境变量：
  COINCALL_CORE_URL          core 地址（默认 http://127.0.0.1:8020）
  COINCALL_DAILY_BUDGET_RAW  L0 日额（提供时 advice 返回 budget_impact 占比）
  COINCALL_ENFORCE_ADVICE    =1 开启 deny 强制（缺省只注入）

挂载说明见同目录 README.md（Claude Code settings.json / ZCode hooks 配置）。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

DEFAULT_CORE_URL = "http://127.0.0.1:8020"
ADVICE_TIMEOUT_S = 3.0
TOOL_PAID_CALL = "paid_service_call"
ENFORCE_DENY_VERBS = {"switch", "insufficient_data"}

try:
    import httpx
except ImportError:  # hook 运行环境缺依赖：fail-open，绝不挡宿主
    print("coincall hook: 缺 httpx，跳过 advice 注入（fail-open）", file=sys.stderr)
    sys.exit(0)


def _parse_toolcall(payload: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """跨宿主宽容解析：Claude 形态 {tool_name, tool_input} / MCP 形态 {tool, arguments} /
    JSON-RPC 形态 {params: {name, arguments}}。"""
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    name = payload.get("tool_name") or payload.get("tool") or params.get("name")
    arguments = (
        payload.get("tool_input") or payload.get("arguments") or params.get("arguments") or {}
    )
    if not isinstance(arguments, dict):
        arguments = {}
    return (str(name) if name else None), arguments


def _fetch_advice(service_id: str, core_url: str) -> dict[str, Any]:
    """GET /advice?current=…（trust_env=False：C-07 系统代理不得劫持 localhost）。"""
    params: dict[str, str | int] = {"current": service_id}
    daily = os.environ.get("COINCALL_DAILY_BUDGET_RAW", "")
    if daily.isdigit() and int(daily) > 0:
        params["daily_budget_raw"] = int(daily)
    with httpx.Client(trust_env=False, timeout=ADVICE_TIMEOUT_S) as http:
        resp = http.get(f"{core_url}/advice", params=params)
        resp.raise_for_status()
        return resp.json()


def _advice_text(advice: dict[str, Any]) -> str:
    """advice → 注入文本（verb/recommend/reason/占比/证据指针，一段人话）。"""
    text = f"[coincall-advice] verb={advice.get('verb')}"
    if advice.get("recommend"):
        text += f" recommend={advice['recommend']}"
    text += f": {advice.get('reason', '')}"
    if advice.get("budget_impact"):
        text += f"（{advice['budget_impact']}）"
    text += f"；证据 {advice.get('evidence') or '无'}"
    return text


def _emit(output: dict[str, Any]) -> None:
    print(json.dumps({"hookSpecificOutput": output}, ensure_ascii=False))


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("stdin 不是 JSON 对象")
    except ValueError as exc:
        print(f"coincall hook: stdin 非法 JSON（{exc}），放行不注入", file=sys.stderr)
        return 0

    tool, arguments = _parse_toolcall(payload)
    if tool != TOOL_PAID_CALL:
        return 0  # 只关心付费调用，其余工具原样放行

    core_url = os.environ.get("COINCALL_CORE_URL", DEFAULT_CORE_URL).rstrip("/")
    service_id = str(arguments.get("service_id", ""))
    try:
        advice = _fetch_advice(service_id, core_url)
    except Exception as exc:  # hook 必须 fail-open：advice 挂了不挡正常付费路径
        _emit(
            {
                "hookEventName": "PreToolUse",
                "additionalContext": (
                    f"[coincall-advice] unavailable（core 不可达: {exc.__class__.__name__}）；"
                    "按 SKILL.md 纪律继续（catalog+quote 核对后调用）"
                ),
            }
        )
        return 0

    text = _advice_text(advice)
    output: dict[str, Any] = {"hookEventName": "PreToolUse", "additionalContext": text}
    if (
        os.environ.get("COINCALL_ENFORCE_ADVICE", "") == "1"
        and advice.get("verb") in ENFORCE_DENY_VERBS
    ):
        output["permissionDecision"] = "deny"
        if advice.get("verb") == "switch":
            output["permissionDecisionReason"] = (
                f"advice 判定 switch：{advice.get('recommend')} 更优"
                f"（{advice.get('reason')}）。请改调 recommend 指向的服务并向用户说明理由；"
                "确需原服务请用户确认后去掉 COINCALL_ENFORCE_ADVICE 重试。"
            )
        else:
            output["permissionDecisionReason"] = (
                f"advice 判定 insufficient_data：{advice.get('reason')}。"
                "分区数据不足时付费属高风险，已拦截；"
                "请用户明确确认后去掉 COINCALL_ENFORCE_ADVICE 重试。"
            )
    _emit(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
