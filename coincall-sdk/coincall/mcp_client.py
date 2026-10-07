"""McpSession —— Python 宿主可复用的 coincall-mcp stdio 会话客户端。

分层定位（2026-10-07 职责分层）：stdio 协议细节属于平台层，宿主 Agent 不该手写——
ZCode/Claude 等宿主自带 MCP 客户端；纯 Python 宿主用本类三行接入：

    from coincall.mcp_client import McpSession
    with McpSession() as mcp:
        tools = mcp.tools_list()
        text, is_error = mcp.call_tool("catalog", {})
        print(mcp.summary_of(text))     # 工具自带 summary 层，直接转述

环境：子进程继承当前 env（COINCALL_* 全部生效），并强制 NO_PROXY=*
（C-07：macOS 系统代理会劫持 localhost 网关调用）。日志只进 stderr（stdout 是协议通道）。
"""

import json
import os
import subprocess
import sys
import tempfile
from typing import Any

PROTOCOL_VERSION = "2024-11-05"


class McpSession:
    """极简 MCP stdio 客户端：单线程严格请求-响应（一行一帧 JSON-RPC 2.0）。"""

    def __init__(
        self,
        command: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        """command 缺省 = 当前解释器跑 coincall.mcp（venv 内零配置）；可覆写为
        ["uvx", "--from", "coincall-sdk", "coincall-mcp"] 等任意启动方式。"""
        merged = dict(os.environ)
        merged["NO_PROXY"] = "*"  # 网关/core 本机地址，防系统代理劫持
        merged["no_proxy"] = "*"
        merged.update(env or {})
        # 防 stderr 管道积压阻塞子进程：落临时文件而非 PIPE
        self._stderr = tempfile.TemporaryFile("w+")  # noqa: SIM115 —— close() 统一关闭
        self.proc: subprocess.Popen[str] | None = None
        self._id = 0
        candidates = (
            [command]
            if command
            else [
                [sys.executable, "-m", "coincall.mcp"],
                ["uvx", "--from", "coincall-sdk", "coincall-mcp"],
            ]
        )
        last_err = ""
        for cmd in candidates:
            try:
                self.proc = subprocess.Popen(  # noqa: S603 —— 内部构造/调用方显式给定的启动命令
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=self._stderr,
                    env=merged,
                    text=True,
                    bufsize=1,
                )
                self.request(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "coincall-mcp-session", "version": "0.1.0"},
                    },
                )
                return
            except (RuntimeError, OSError) as exc:
                last_err = f"{cmd[0]}: {exc}"
        raise RuntimeError(
            f"coincall-mcp 子进程无法启动（{last_err}）——请在装有 coincall 包的环境里运行"
        )

    # -- 协议 --

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        rid = self._id
        frame: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            frame["params"] = params
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("MCP 子进程未启动（初始化失败）")
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
        """返回 (text, is_error)——错误也是结构化人话文本，不抛异常。"""
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content") or [{}]
        text = str(content[0].get("text", "")) if isinstance(content[0], dict) else ""
        return text, bool(result.get("isError"))

    # -- summary 层（工具响应自带一行人话；宿主直接转述） --

    @staticmethod
    def summary_of(text: str, *, fallback_chars: int = 160) -> str:
        """取工具响应的 summary 字段；无 summary（老版本/非 JSON）时截断兜底。"""
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return text[:fallback_chars]
        if isinstance(payload, dict) and payload.get("summary"):
            return str(payload["summary"])
        return text[:fallback_chars]

    # -- 生命周期 --

    def close(self) -> None:
        try:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        finally:
            self._stderr.close()

    def __enter__(self) -> "McpSession":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
