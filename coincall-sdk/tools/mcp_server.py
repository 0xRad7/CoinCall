#!/usr/bin/env python3
"""兼容入口：MCP server 唯一事实源已迁移到 coincall/mcp.py（打包入口 coincall-mcp）。

保留本文件仅为旧引用（AGENTS.md/README 的 `uv run python tools/mcp_server.py`）——
它只做仓内直跑的 sys.path 引导后转发；一切行为以 coincall/mcp.py 为准（双副本曾漂移：
timeout/mint 提示两处不一致，2026-10-07 收敛）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coincall.mcp import (  # noqa: F401 —— 转发导出（旧 import 兼容）
    ADVICE_TIMEOUT_S,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    TOOL_NAMES,
    TOOL_NAMES_ORDERED,
    TOOLS_SPEC,
    CoinCallMcpServer,
    build_client_from_env,
    main,
)

if __name__ == "__main__":
    main()
