# PreToolUse Hook 样例：付费调用前自动注入 advice

`pretooluse.py` 是 CoinCall 的 **宿主侧介入通道**演示件：Agent 每次要调
`paid_service_call` 时，hook 先问 core 决策引擎（`GET /advice?current=<service_id>`），
把 `verb + reason` 人话作为 `additionalContext` 注回对话——Agent 在花钱前"听劝"。

三个介入通道的关系：**service_quote 内嵌 advice**（默认通道，全 MCP 宿主即刻生效）→
**本 hook**（宿主级强制，甚至可 deny）→ **SDK `Client.advice()`**（编程路径自由组合）。

## 行为契约（安全优先）

| 场景 | 行为 |
|---|---|
| 工具 ≠ `paid_service_call` | 静默放行（空 stdout，exit 0） |
| 默认模式（无 ENFORCE） | 仅注入 `additionalContext`；**绝不 deny、也绝不 auto-allow**（allow 会绕过宿主人审——付费工具永不由 hook 放行） |
| `COINCALL_ENFORCE_ADVICE=1` + `verb=switch` | `permissionDecision=deny`，理由点名更优的 recommend 服务（强制听劝） |
| `COINCALL_ENFORCE_ADVICE=1` + `verb=insufficient_data` | `deny`（分区无数据时盲付高风险） |
| core 不可达 / stdin 非法 / 缺 httpx | **fail-open**：注入 unavailable 提示后 exit 0——advice 是增强不是依赖 |

输入宽容解析三种形态：Claude Code `{tool_name, tool_input}`、MCP `{tool, arguments}`、
JSON-RPC `{params: {name, arguments}}`。

## Claude Code 挂载（settings.json）

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "paid_service_call",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project /path/to/coincall-sdk python /path/to/coincall-sdk/skills/coincall-consumer/hooks/pretooluse.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

（Claude Code 的 matcher 需匹配工具名；本脚本自身也做了 `tool_name` 过滤，双保险。）

## ZCode 挂载说明

ZCode 支持 hooks（用户级 `~/.zcode/settings.json` 或项目级 `.zcode/settings.json`），
事件 `PreToolUse`、matcher 匹配工具名、`type: command` 执行本脚本，字段结构同上
（以宿主当前文档为准）。两点如实注明：

- **`updatedInput`（改写入参）在 ZCode 未证实**——本脚本不依赖它，输出走
  `additionalContext` 注入兜底（两宿主语义等价：Agent 都能在决策前看到建议文本）；
- 输出中的 `permissionDecision: deny` 按 Claude Code 语义编写，ZCode 侧若不支持
  该字段，退化为"建议文本注入 + 日志可见"（fail-open，不会误放行）。

## 环境变量

```bash
COINCALL_CORE_URL=http://127.0.0.1:8020   # core 地址（默认同左）
COINCALL_DAILY_BUDGET_RAW=200000          # 可选：提供时 advice 返回 budget_impact 占比
COINCALL_ENFORCE_ADVICE=1                 # 可选：开启 switch/insufficient_data 的 deny 强制
```

macOS 代理环境注意：脚本内 httpx 显式 `trust_env=False`（C-07），无需额外 NO_PROXY。

## 手工验证

```bash
echo '{"tool_name":"paid_service_call","tool_input":{"service_id":"svc_rad_ai","params":{}}}' \
  | COINCALL_CORE_URL=http://127.0.0.1:8020 python pretooluse.py
# → {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "[coincall-advice] verb=… "}}

# 强制听劝演示（verb=switch/insufficient_data 时 deny）：
echo '…同上…' | COINCALL_ENFORCE_ADVICE=1 python pretooluse.py
```

自动化用例见 SDK 仓 `tests/test_hooks.py`（回环 /advice 假件 + 子进程真跑断言）。
