"""coincall-consumer PreToolUse hook 样例（skills/…/hooks/pretooluse.py）。

A2 说明：hook 是独立子进程、自建 httpx 客户端，无法注入 MockTransport——
用**进程内 127.0.0.1 回环** ThreadingHTTPServer 承载 /advice 假件（零外网、
确定性），子进程经 env COINCALL_CORE_URL 指向它。stdin/stdout 契约按
Claude Code PreToolUse 形态断言；另含 SKILL.md 纪律句的文档存在性断言。
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = REPO_ROOT / "skills" / "coincall-consumer" / "hooks" / "pretooluse.py"
SKILL_MD = REPO_ROOT / "skills" / "coincall-consumer" / "SKILL.md"
HOOKS_README = REPO_ROOT / "skills" / "coincall-consumer" / "hooks" / "README.md"

pytestmark = pytest.mark.unit

ADVICE_SWITCH = {
    "verb": "switch",
    "recommend": "svc_better",
    "reason": "收入明显领先，较你选的 svc_rad_ai",
    "alternatives": [{"id": "svc_third", "why": "综合分低 0.20"}],
    "budget_impact": "0.01 / 日额 0.05",
    "evidence": "/decision/explain/svc_better",
    "as_of": "2026-10-07T00:00:00+00:00",
    "category": "other",
}
ADVICE_INSUFFICIENT = {
    "verb": "insufficient_data",
    "recommend": None,
    "reason": "分区暂无足够数据，建议按价格与服务描述自选",
    "alternatives": [{"id": "svc_rad_ai", "why": "价格 0.01"}],
    "budget_impact": None,
    "evidence": None,
    "as_of": "2026-10-07T00:00:00+00:00",
    "category": "other",
}


class _AdviceServer:
    """回环 /advice 假件：类属性 advice_json 控制响应。"""

    advice_json: ClassVar[dict] = ADVICE_SWITCH
    requests: ClassVar[list[str]] = []

    def __init__(self) -> None:
        handler = _make_handler(type(self))
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _make_handler(owner: type) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            owner.requests.append(self.path)
            if self.path.startswith("/advice"):
                body = json.dumps(owner.advice_json).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: object) -> None:  # 静音（测试输出清洁）
            return

    return Handler


def _run_hook(
    payload: dict, server: _AdviceServer, *, enforce: str = ""
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("COINCALL_")}
    env["COINCALL_CORE_URL"] = server.url
    if enforce:
        env["COINCALL_ENFORCE_ADVICE"] = enforce
    return subprocess.run(  # noqa: S603 —— 本仓 venv python 执行本仓样例脚本，输入受控
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )


PAID_CALL_PAYLOAD = {
    "tool_name": "paid_service_call",
    "tool_input": {"service_id": "svc_rad_ai", "params": {"query": "BTC"}},
}


def _hook_output(proc: subprocess.CompletedProcess[str]) -> dict:
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_hook_enforce_switch_denies_and_names_recommend() -> None:
    """ENFORCE=1 + verb=switch → deny，理由点名 recommend 服务（强制听劝演示）。"""
    server = _AdviceServer()
    try:
        server.advice_json = ADVICE_SWITCH
        proc = _run_hook(PAID_CALL_PAYLOAD, server, enforce="1")
        out = _hook_output(proc)
        spec = out["hookSpecificOutput"]
        assert spec["permissionDecision"] == "deny"
        assert "svc_better" in spec["permissionDecisionReason"]
        assert "verb=switch" in spec["additionalContext"]
        # advice 请求带 current=想调的服务
        assert any("current=svc_rad_ai" in p for p in server.requests)
    finally:
        server.stop()


def test_hook_enforce_insufficient_denies() -> None:
    """ENFORCE=1 + verb=insufficient_data（风险场景）→ deny，防数据不足时盲付。"""
    server = _AdviceServer()
    try:
        server.advice_json = ADVICE_INSUFFICIENT
        proc = _run_hook(PAID_CALL_PAYLOAD, server, enforce="1")
        spec = _hook_output(proc)["hookSpecificOutput"]
        assert spec["permissionDecision"] == "deny"
        assert "insufficient" in spec["permissionDecisionReason"]
        assert "分区暂无足够数据" in spec["additionalContext"]
    finally:
        server.stop()


def test_hook_default_mode_injects_context_but_never_denies_or_allows() -> None:
    """默认（无 ENFORCE）：只注入 additionalContext——绝不 deny、也绝不 auto-allow 付费调用。"""
    server = _AdviceServer()
    try:
        server.advice_json = ADVICE_SWITCH
        proc = _run_hook(PAID_CALL_PAYLOAD, server)  # 不设 ENFORCE
        spec = _hook_output(proc)["hookSpecificOutput"]
        assert "permissionDecision" not in spec  # 不越权改判（allow 会绕过人审）
        assert "verb=switch" in spec["additionalContext"]
        assert "svc_better" in spec["additionalContext"]
    finally:
        server.stop()


def test_hook_non_paid_tool_passes_through_silently() -> None:
    """非 paid_service_call 工具：静默放行（空 stdout、exit 0、不打 /advice）。"""
    server = _AdviceServer()
    try:
        proc = _run_hook({"tool_name": "catalog", "tool_input": {}}, server, enforce="1")
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""
        assert server.requests == []  # 未触 advice
    finally:
        server.stop()


def test_skill_documents_advice_discipline_and_hook_mount() -> None:
    """SKILL.md 第 3 步含 switch 纪律句；hooks/README 提供宿主挂载说明。"""
    skill = SKILL_MD.read_text(encoding="utf-8")
    assert "verb=switch 时改调 recommend" in skill
    readme = HOOKS_README.read_text(encoding="utf-8")
    assert "PreToolUse" in readme and "settings.json" in readme
    assert "additionalContext" in readme  # ZCode 兜底通道注明
