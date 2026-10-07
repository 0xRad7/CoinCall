# 决策层形态修正调研：从"数据接口"到"建议接口"（decision-as-advice）

> 状态：调研稿（2026-10-01）。发起人命题（浓缩）："决策返回的数据太复杂——决策的消费者是 Agent 的工具调用决策点，不是人眼。我们相当于是一层 **Agent 的使用提示层**，可能需要体现在 **PreToolHook** 中：在 Agent 调用平台能力前，进行介入建议。"
> 本文只调研与出设计，不改任何代码。现状基线：MCP 五工具（coincall-sdk/tools/mcp_server.py）、决策三信号接口（coincall-core/app/modules/decision.py）、网关七步时序（coincall-gateway/app/modules/call_route.py）、L0 策略引擎（coincall-sdk/coincall/policy.py）、skill 纪律"先 quote 后 call"（coincall-sdk/skills/coincall-consumer/SKILL.md）。

---

## 1. 介入点能力盘点（外部机制调研）

### 1.1 MCP 侧：规范没有"调用前"扩展点，但有四条相邻通道

**tools/call 本身没有 pre-call 拦截原语。** 规范定义的调用时序是 `tools/list →（LLM 选工具）→ tools/call → 结果`，中间没有宿主↔server 的协商步。但四条通道与"建议层"相关：

1. **服务端输入校验 + isError 文本**。规范 Security Considerations 明确 "Servers MUST: Validate all tool inputs"。校验失败有两种回法：JSON-RPC 协议错误（-32602，给客户端开发者看）或 tool execution error（`isError: true` + 文本，**该文本会被喂给模型**）。所以"inputSchema 之外的服务端校验，用人话文本返回"**就是规范认可的建议层形态**——CoinCall 网关 402 人话指引经 MCP server 的 isError 转发，走的正是这条通道。它是"事中/事后建议"（请求已到达，钱未必已花）。
2. **Tool annotations（声明式前置信号）**。`readOnlyHint / destructiveHint / idempotentHint / openWorldHint` 等注解让客户端决定自动放行还是弹人确认；规范同时要求 "Clients MUST consider tool annotations to be untrusted"。给 `paid_service_call` 标 `destructiveHint: true` 是一行成本挂进"宿主自动确认"通道（注：2025-06-18 规范页只说 annotations 存在，字段清单以 MCP schema 为准）。
3. **Elicititation（2025-06-18 新增）**：server 可在操作中途经客户端向**用户**要结构化输入（扁平 schema，accept/decline/cancel 三态）。它能做"付费前人确认"，但（a）方向是向人要输入、不是向 Agent 给建议；（b）依赖宿主声明 elicitation capability。不推荐作为主通道，我们的 stdio server 也尚未声明它。
4. **Sampling**：server 反向请求**客户端的 LLM** 补全（`sampling/createMessage`）。不是 pre-tool 介入点，但它的 `modelPreferences.hints` 设计哲学值得引用——规范原话："**Hints are advisory—clients make final model selection**"。MCP 自己在"选模型"这个同构问题上就采用了"建议优先、消费者终裁"的分层。

### 1.2 Claude Code / ZCode 类 CLI：PreToolUse hook 是最完整的介入点

Claude Code 官方 hooks 文档（ZCode 同源支持，见下）给出的 PreToolUse 形态：

- **输入**：stdin 收 `{tool_name, tool_input, tool_use_id, session_id, …}`；`matcher` 按工具名匹配（MCP 工具形如 `mcp__coincall__paid_service_call`，支持正则）。
- **返回**（JSON `hookSpecificOutput`）：
  - `permissionDecision`: `"allow" | "deny" | "ask" | "defer"` + `permissionDecisionReason`；
  - **`updatedInput`：直接改写工具参数后再执行**（官方推荐用于出站脱敏/变换——正是"把 service_id 从 svc_X 改成 svc_Y"所需的语义）；
  - **`additionalContext`：作为 system reminder 注入对话**，模型下一轮可见（上限 1 万字符）；
  - 退出码 2 = 硬阻断（任何 JSON 决定都盖不过）。
- 官方还有一句关键提醒："**use the permission system rather than a hook to enforce a hard allow or deny**"——hook 适合建议，权限系统适合强制。这直接支持本文 §2.3 的分层主张。
- 相邻事件：`PostToolUse`（additionalContext + `updatedToolOutput` 改写结果）、`UserPromptSubmit`（纯注入上下文）。

**ZCode 侧实证**（本地 zcode-guide:diagnosing-hooks skill）：ZCode 支持同样七个事件（`SessionStart / UserPromptSubmit / PreToolUse / PermissionRequest / PostToolUse / PostToolUseFailure / Stop`），PreToolUse 可返回 `allow/ask/deny` 决定 + `additionalContext`；配置在 `~/.zcode/cli/config.json` 或工作区 `.zcode/config.json`（`hooks.enabled: true`），matcher 为大小写敏感正则匹配工具名，stdout 走严格 JSON schema 校验（**多余键直接判失败**）。⚠️ 诚实标注：**`updatedInput` 在 ZCode 的 hook 输出 schema 中未明示**（Claude Code 官方文档有此字段），演示前需实测；兜底方案是 `additionalContext` 注入建议 + `ask` 决定（两者 ZCode 都明确支持）。

### 1.3 OpenAI Agents SDK：guardrail 分输入级与工具级

- **Input guardrails**：默认与首轮 LLM 调用并行（`run_in_parallel=True`，可能已花 token）；`run_in_parallel=False` 则阻塞先行。tripwire 触发抛 `InputGuardrailTripwireTriggered` 终止执行。只覆盖链上第一个 agent 的输入，**不覆盖每次工具调用**。
- **Tool guardrails**（我们真正关心的形态）：挂在 `FunctionTool` 上的 input tool guardrails **"run before the tool executes and can skip the call, replace the output with a message, or raise a tripwire"**；且本地 MCP server 暴露的工具可批量挂（"the SDK attaches those lists to every tool exposed by that server"）。这就是 Agent 进程内的 PreToolUse。限制：handoff 走另一管线不经过它；托管工具（WebSearchTool 等）也跳过。
- 挂载条件：消费者用 Python SDK 组装 Agent（CoinCall 接入形态 B）。MCP 直挂宿主（形态 A）覆盖不到。

### 1.4 LangGraph / LangChain 1.0：middleware 是现行推荐，pre-hook 仍在 RFC

- 社区 RFC（langgraph issue #8102）主张给 ToolNode 增加"pre-execution tool call interception hooks for policy"，动机正是"给团队稳定的调用前拦截点，避免脆弱的内部方法子类化"——说明这个介入点在 LangGraph 里至今仍是补丁位。
- 现行推荐路径：`create_react_agent` 的 **agent middleware `wrapToolCall`**——可修改 `ToolCall.args`、**不调用 next() 直接返回 ToolResult 即跳过执行**（LangChain 自带的 TODO middleware 是官方参考实现）。另有 `interrupt()` 做 human-in-the-loop 暂停。
- 挂载条件：同 1.3，仅自研 Agent 路径。

### 1.5 AIsa 的 quote 步骤：同类的"流程纪律型"提示层

AIsa skill 工作流 `search → schema → quote → call`，quote 的纪律原文："**Quote does not execute and is not approval to execute** … **Re-quote if tools, arguments, or scope change**"。它与 CoinCall 的 `service_quote` 完全同构：**强制"花钱前看价"的不是拦截器，而是 skill 写就的工作流纪律**（靠 LLM 遵循步骤）。这证明提示层可以纯靠纪律成立，也暴露其边界：纪律是概率性的（模型可能跳步），没有宿主/服务端的结构保证。本次形态修正="在纪律之外补结构化介入点"，不是替代 quote。

### 1.6 介入点能力对照表

| 机制 | 所在层 / 时机 | 改写参数 | 拒绝 | 注入建议文本 | CoinCall 挂载（成本 / 覆盖） |
|---|---|---|---|---|---|
| MCP `tools/call` 服务端校验 → `isError` 文本 | server / 调用中 | 否 | 是（execution error） | 是（文本直达模型） | **已挂**（402 人话现状）；仅失败路径 |
| MCP tool annotations（`destructiveHint` 等） | 声明 / tools/list 时 | 否 | 间接（触发宿主人确认） | 否 | 一行改动；全部 MCP 宿主；仅信号无内容 |
| MCP elicitation | server→用户 / 调用中 | 否 | 是（decline） | 向人而非 Agent | 不推荐：依赖宿主 capability，方向不符 |
| MCP sampling hints | server→client LLM | 否 | 否 | 间接 | 不适用；其 "hints are advisory" 哲学可引用 |
| Claude Code **PreToolUse** hook | 宿主 / 调用前 | **是（updatedInput）** | 是（deny/exit 2） | **是（additionalContext）** | 用户配置一次；Claude Code/ZCode 等宿主；演示效果最强 |
| ZCode PreToolUse hook | 宿主 / 调用前 | 未证实（需实测） | 是（deny） | 是（additionalContext） | 同上；`ask` 兜底 |
| OpenAI Agents SDK **tool input guardrail** | Agent 进程 / 工具执行前 | 间接（skip/replace） | 是（tripwire） | 是（replace output 文本） | 仅 SDK 形态消费者（形态 B） |
| LangChain 1.0 `wrapToolCall` | Agent 进程 / 工具执行前 | **是（改 args）** | 是（不调 next()） | 是 | 仅自研 Agent 路径 |
| AIsa 式 quote 纪律（CoinCall `service_quote`） | 流程 / Agent 自愿 | 否 | 否 | 是（返回内容自由） | **已挂**（skill+工具描述）；概率性遵守 |
| （对照）L0 白名单/预算 | SDK 客户端 / 调用前 | 否 | **强制** | 人话拒绝理由 | **已挂**；形态 B/C 全覆盖 |
| （对照）网关咽喉（402/schema/影子闸门） | 服务端 / 调用中 | 否 | **强制** | 是（402 body 人话） | **已挂**；100% 消费者 |

**读法**：能同时"改写参数 + 注入文本"且在"调用前"的，只有宿主 hook（1.2）与自研 Agent middleware（1.3/1.4）；我们**自有地盘**内能做的只有 MCP server 侧（内嵌返回、annotations、isError）与网关侧（402 内嵌）。没有单一介入点全覆盖——这正是"多通道同引擎"方案（§3）的依据。

---

## 2. 形态抽象：从"数据接口"到"建议接口"

### 2.1 接口形状：全量指标 vs 极简判定

现状 `/decision/services` 每行约 10 个顶层字段 × 3 证据组件约 25 个子字段（score/components/proof/formula/weights…），响应级还带公式与归一化说明。它的消费者是控制台总览（coincall-console/src/pages/Overview.tsx 在用）——**人看板，证据可点验是核心价值**。

两种给 Agent 的形状对比：

- **A（现状复用）**：Agent 拉 `/decision/services` 自己算。问题：(1) 35+ 字段/行挤占上下文，MCP 链路上还要过一道 JSON 文本化；(2) "算分→选服务"的推理在每个 Agent 重复实现且会漂移；(3) 没有人话理由，Agent 无法向用户解释"为什么选它"。
- **B（极简判定式）**：`verb + recommend + reason + alternatives + budget_impact`，证据退为二级指针。

**业界佐证**（B 是主流）：

- **Not Diamond `/modelSelect`**：请求给"消息 + 候选模型列表"，返回被选模型与路由理由，**只推荐不执行**——与"建议接口"同构（OpenRouter 的模型路由亦由其驱动）。⚠️ 诚实标注：其 docs 站点当前 404，精确字段名（selected_model/routing reason）来自搜索摘要，未能回读官方文档核实。
- **OpenRouter provider routing**：请求可携带 `provider.order / sort / preferred_max_latency / preferred_min_throughput`（官方语义："**deprioritizes rather than excludes**"——建议档）与 `max_price`（"can prevent a request from running"——强制档）。**"偏好建议"与"硬约束"在同一 API 里显式分档**，是 §2.3 分层的最佳业界先例。其路由依据（5 分钟滚动窗口的延迟/吞吐分位）只作内部决策输入，不整包返回给调用方。
- **MCP sampling modelPreferences**：hints "advisory—clients make final selection"，capability priorities（cost/speed/intelligence 0-1）+ 少量 hint，不是全量模型评测数据。

**结论：建议接口 = 判定（verb/recommend）+ 人话理由 + ≤2 个备选一句话 + 少量数字上下文（价格/预算占比）+ 证据指针（不给证据本体）**。证据本体的消费者是人和审计路径，继续留在 `/decision/explain` 与看板。

### 2.2 介入时机：谁调建议接口

| 候选 | 机制 | 覆盖面 | 侵入性 | 时效 | 演示效果 |
|---|---|---|---|---|---|
| a) 宿主 PreToolUse hook | hook 拦 `mcp__coincall__paid_service_call`，调 `/advice`，`additionalContext` 注入理由（或 `updatedInput` 改 service_id） | 配置了 hook 的宿主（Claude Code/ZCode） | 用户配置一次（~10 行 JSON + 脚本） | **花钱前**，可改写参数 | **最强**（可见"Agent 被建议后当场改调"） |
| b) 网关响应内嵌 | `/call` 的 402 body / 响应头携带 advice | 100% 消费者（含 SDK 直连） | **零** | 402=失败后；200 头=成功后 | 中（多在失败路径出现，像补救不像建议）；且 MCP 链路吃掉 HTTP 头，仅 SDK 消费者可见 |
| c) MCP 工具内嵌 | `service_quote`/`catalog` 返回内嵌 `advice` 字段，工具描述引导先问 | **全部 MCP 宿主，零配置** | 零（平台侧改自己的 server） | 花钱前（quote 是 skill 纪律必经步） | 强（quote 返回里直接看到"你正看的服务：建议换 Y"） |

三通道不互斥。**主推 c 为默认通道（自有地盘、零宿主依赖、搭 quote 便车），a 为高光演示与高阶配置，b 后置为 SDK 直连消费者的兜底**。理由：c 的介入点天然存在于"先 quote 后 call"纪律中（AIsa 同构验证过流程可行性），而 a 证明"提示层=PreToolHook"的命题、b 证明服务端零依赖兜底——三者共用同一个 `/advice` 引擎，成本边际递减。

### 2.3 "建议"与"强制"的边界

现有两层强制：L0 白名单/三重预算（客户端，`policy.check` 硬拒）、网关咽喉（服务端 402/schema 校验/坏账黑名单/影子闸门）。二者覆盖"安全与钱"，不覆盖"选得好不好"。

**主张：提示层默认纯建议（不拦、不改），强制语义留在已有两层。** 论据：

1. 宿主规范的原话背书：Claude Code 明示 hook 不该承担 hard allow/deny（"use the permission system rather than a hook"）；MCP 要求 annotations 视为 untrusted——生态共识是"调用前介入点做建议，权限系统做强制"。
2. 业界分层先例：OpenRouter `preferred_*`（建议，deprioritize）vs `max_price`（强制，exclude）。
3. 产品叙事自洽：CoinCall 是开放市场不是围墙花园——"Agent 可不听"恰是可信度来源；且建议错了不造成资金损失（L0/网关仍兜底资金安全）。

**可配置升级**：`/advice?mode=enforce` 或 hook 配 `deny`，把"分区外/劣质服务"升级为拒绝——但这属于宿主/用户侧选择（如 `COINCALL_ENFORCE_ADVICE=1` 让 hook 返回 deny），平台端点保持 `advise` 语义。三档谱系：**建议（advice，新）/ 消费者强制（L0）/ 平台强制（网关咽喉）**——提示层补的正是中间缺失的"温和档"。

---

## 3. CoinCall 适配方案草案

### 方案一（主推）：同引擎双视图——新建 `GET /advice` 极简端点 + service_quote 内嵌 advice + PreToolUse hook 演示件

**核心判断：`/decision/services` 保留给人看板不动（console 在用、证据可点验是赛题叙事），新建 `GET /advice` 给 Agent（同一 `_build_context/_finalize_rows` 引擎的判定式投影）。** 拆分不是两套逻辑，是两个视图——advice 的 recommend 永远可从 decision 排序复算，"建议不是黑箱推荐，是排序的人话投影"本身即可辩。

**端点形状**：

```
GET /advice?category=translation&current=svc_translation_a&daily_budget_raw=200000&as_of=
```

- `current`（可选）：Agent 正要调的服务——有它才能出 `switch/keep` 判定（hook 与内嵌通道的关键参数）；无它就是纯推荐。
- `daily_budget_raw`（可选）：来自 MCP 路径的 L0 现值，用于 budget_impact 占比。

**返回示例**：

```json
{
  "verb": "switch",
  "recommend": "svc_translation_b",
  "current": "svc_translation_a",
  "confidence": "medium",
  "margin": 0.23,
  "reason": "svc_translation_b：窗口收入最高（12.4 USDT / 7 个支付方）、履约 93%（p95 1.8s）、35 分钟前活跃；比 svc_translation_a 总分高 0.39",
  "alternatives": [
    {"service_id": "svc_translation_a", "why": "便宜 40% 但近 7 天无成功履约记录", "price_raw": "30000", "score": 0.42}
  ],
  "budget_impact": {"price_raw": "50000", "price": "0.05", "daily_budget_raw": "200000", "share_of_daily": 0.25},
  "evidence": {
    "explain": "/decision/explain/svc_translation_b",
    "weights": {"revenue": 0.5, "fulfillment": 0.3, "freshness": 0.2},
    "formula": "score = 0.5*revenue + 0.3*fulfillment + 0.2*freshness",
    "as_of": "2026-10-01T09:00:00Z",
    "window_hours": 168
  },
  "degraded": []
}
```

字段语义：

- `verb`：`recommend`（无 current 的纯推荐）/ `keep`（current 即最优）/ `switch`（更优且超阈值）/ `indifferent`（分差在死区内，不值得换）/ `insufficient_data`（分区无数据或候选 <2，明示按价格兜底）。**Agent 只需读 verb + reason 即可行动。**
- `confidence` + `margin`：margin=建议服务与次优（或与 current）的分差，映射 high(≥0.15)/medium(≥0.05)/low；阈值写入响应与文档，可辩。
- **`reason` 用确定性模板拼接**（组件值→中文短句），**不引入 LLM 生成**——保住"公式透明可辩"资产，模板可单测。
- `evidence` 只给指针与权重快照，不给证据本体。

**介入点实现**：

1. **service_quote 内嵌（默认通道）**：`service_quote_payload()` 增加 `advice` 子对象——MCP server 侧调 core `/advice?current={service_id}&daily_budget_raw={L0现值}`（core 不可达时降级为无该字段，quote 其余功能不受影响）。工具描述加一句："返回含 advice：若 verb=switch 且无特殊理由，改调 recommend 并向用户说明"。**零宿主依赖，所有 MCP 消费者即刻生效。**
2. **PreToolUse hook（演示高光 + 高阶配置）**：交付 `~/.coincall/advice_hook.py` 样例 + 配置片段：

```json
{ "hooks": { "enabled": true, "events": { "PreToolUse": [
  { "matcher": "mcp__coincall__paid_service_call",
    "hooks": [{"type": "command", "command": "python ~/.coincall/advice_hook.py", "timeoutMs": 5000}] }
] } } }
```

hook 逻辑：stdin 读 `tool_input.service_id` → GET core `/advice?current=…` → verb=switch 时输出 `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "additionalContext": "CoinCall 建议：…（reason）"}}`（ZCode 确定支持）；Claude Code 环境可改用 `updatedInput` 直接把 service_id 改写为 recommend（需实测 ZCode 支持度，兜底 additionalContext）。强制档：`COINCALL_ENFORCE_ADVICE=1` 时返回 `deny` + 理由。

**Agent 侧配合**：SKILL.md 操作流程第 3 步（看价）追加一句——"读 service_quote 返回的 advice 字段：verb=switch 且无特殊理由 → 改调 recommend 并向用户说明一句理由"。工具描述同步。不改 L0、不改网关。

**演示故事（可现场演示）**：Agent 接"翻译这段新闻"→ catalog 看到两个翻译服务 → Agent 顺口准备调 svc_translation_a → service_quote 返回 advice：`verb=switch, recommend=svc_translation_b, reason=…`→ Agent 改调 svc_translation_b → 收据正常。加演 hook 版：PreToolUse 弹建议、Agent 当场改参数。控制台并排展示 /decision/services 看板（同一分数同一排序）——"同一真相，两种视图：人看证据，Agent 听建议"。

**改动面**：core `decision.py` +advice 视图与 reason 模板（~150 行，复用现有 `_Context/_finalize_rows`）0.5d；sdk `mcp_server.py` +advice 字段（~30 行含降级）+ SKILL.md 一句 0.5d；hook 样例脚本+配置文档（~60 行，纯文档交付物）0.25d；console 零改动。**合计 ~1.25d**。

### 方案二（备选 A）：网关 402 内嵌建议——零宿主依赖的服务端介入

- 形状：`build_challenge()` 的 402 body 增加 `advice` 字段（网关同步调 core `/advice?current={service_id}`，core 不可达静默省略）；另在 200 成功响应加 `X-CoinCall-Advice` 头（RFC 7234 Warning 风格）供 SDK 直连消费者读。
- 优点：覆盖 100% 消费者（含不经 MCP 的 SDK 调用），零宿主配置；与既有"402 人话指引"风格连续。
- 缺点：402 是失败路径（建议出现在"已经错了一次"之后，演示叙事弱）；200 响应头对 MCP 链路透明（server 吃掉 HTTP 头，Agent 看不见），仅形态 B 受益；网关增加对 core 的同步依赖（虽有降级）。
- 改动面：gateway `schemes.py/call_route.py` ~40 行 + 测试 0.5d；仍需方案一的 `/advice` 端点作为数据源（网关不重复实现排序）。
- 定位：**作为方案一的后置补充**（第二期给 SDK 直连消费者），不单独成案。

### 方案三（备选 B）：宿主 hook 为主介入点，不新建端点

- 形状：hook 脚本直接调现有 `/decision/services?category=…`，脚本内自行算 top1/margin/理由并注入。
- 优点：core 零改动，最快出演示。
- 缺点：reason 生成逻辑散落在每个 hook/消费者里必然漂移（与"公式写进响应"的可辩设计冲突）；35 字段全量传输挤占 hook 的 5s 时限与上下文；非 hook 消费者（MCP 直挂）无份。
- 判断：**恰好反证方案一的分工正确——判定与人话化必须在服务端一处实现，客户端（hook/MCP/SDK）只做薄注入**。此案仅当演示紧急、且只需 hook 单通道时取用。

### 3.1 三方案对照

| | 方案一（主推） | 方案二（网关内嵌） | 方案三（纯 hook） |
|---|---|---|---|
| 新端点 | `GET /advice`（新） | 复用方案一端点 | 无 |
| 介入点 | quote 内嵌（默认）+ hook（高光） | 402 body / 200 响应头 | PreToolUse hook |
| 覆盖 | 全部 MCP 宿主 + hook 宿主 | 100%（头部仅 SDK 可见） | 仅配置 hook 的宿主 |
| 侵入性 | 平台侧改；宿主零配置（hook 可选） | 平台侧改；零宿主配置 | 用户配 hook |
| 演示叙事 | 花钱前被建议、当场改调 | 失败后给替代（像补救） | 同方案一 hook 部分 |
| 改动面 | ~1.25d | +0.5d（依赖方案一端点） | 0（core）+ hook 脚本 |

---

## 4. 诚实边界与未决问题

1. **ZCode PreToolUse 的 `updatedInput` 未实测**：Claude Code 官方文档明确支持；ZCode hook 输出 schema 严格（多余键判失败）且本地文档未列出该键。演示前必须实测，兜底 `additionalContext + ask`（ZCode 明确支持）。
2. **Not Diamond 精确响应字段未核实**（docs 站 404，仅搜索摘要）；OpenRouter 字段已回读官方文档核实；MCP 规范全部回读原文。
3. **`/advice` 是无状态快照**：不感知"Agent 的任务意图"，建议仅基于分区排序 + current 差值。若未来要按任务语义选服务（翻译→翻译类目），需要 Agent 显式传 category——工具描述引导即可，不做自由文本意图解析（保确定性）。
4. **冷启动**：分区无履约/收入数据时 verb=insufficient_data 并明示"按价格与新近度兜底"，confidence=low——不给貌似权威的建议。
5. **建议层的滥用面**：advice 会影响 Agent 选择 → 平台理论上可偏袒（与履约数据"平台可伪造"同源的诚实边界）。缓解：advice 响应内嵌 weights/formula/explain 指针，建议可被第三方从公开数据复算。
6. `share_of_daily` 等预算口径只在消费者传入 budget 时出现，避免平台端点暗示存储了消费者隐私。

---

## 5. 来源清单

**规范与官方文档（已回读原文）**

1. Claude Code Hooks 官方参考（PreToolUse 输入/输出、permissionDecision/updatedInput/additionalContext、exit 2、"use the permission system rather than a hook"）：https://code.claude.com/docs/en/hooks
2. MCP 规范 2025-06-18 — Tools（tools/call、isError 两级错误、annotations untrusted、MUST validate inputs）：https://modelcontextprotocol.io/specification/2025-06-18/server/tools
3. MCP 规范 2025-06-18 — Elicitation（server 向用户要输入、accept/decline/cancel）：https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation
4. MCP 规范 2025-06-18 — Sampling（"Hints are advisory—clients make final model selection"）：https://modelcontextprotocol.io/specification/2025-06-18/client/sampling
5. OpenAI Agents SDK — Guardrails（input/tool guardrails，"run before the tool executes and can skip the call, replace the output, or raise a tripwire"，本地 MCP server 工具可挂）：https://openai.github.io/openai-agents-python/guardrails/
6. OpenRouter — Provider Routing（provider.order/sort/preferred_max_latency="deprioritizes rather than excludes"/max_price="can prevent"、5 分钟滚动窗口分位指标）：https://openrouter.ai/docs/guides/routing/provider-selection

**Issue/社区（搜索摘要级，链接可回查）**

7. LangGraph RFC #8102 "Pre-execution tool call interception hooks for policy enforcement"：https://github.com/langchain-ai/langgraph/issues/8102
8. LangChain 1.0 agent middleware（wrapToolCall 可改 args / 不调 next() 即跳过执行；TODO middleware 参考实现）——dev.to 概览与 fossies 镜像的 langchain/agents/middleware/todo.py（搜索摘要；官方 docs.langchain.com 的 middleware 页面本轮 404，未能回读原文，已标注）：
   - https://dev.to（"LangChain's New Middleware: The Missing Piece for Production Agents"，2025-09）
   - https://fossies.org（langchain /agents/middleware/todo.py 源码镜像）
9. OpenAI Agents SDK 生产实践（tool guardrails 包装函数工具，搜索摘要）：https://www.bisoft.com.tr
10. Claude Code settings 参考（permissions allow/deny/ask 与 hook 联动，搜索摘要）：https://hookstack.app

**未能核实（如实标注）**

11. Not Diamond `/modelSelect`（"候选模型+消息→selected_model+路由理由，不执行"）——docs.notdiamond.ai 本轮 404，仅搜索摘要；SDK 仓库 https://github.com/Not-Diamond/not-diamond-typescript 存在。
12. Martian Model Router API 响应形状——未找到可回读文档，放弃引用。

**本地实证（无需外部来源）**

13. ZCode hooks 机制（七事件、hooks.enabled、严格 JSON schema、PreToolUse allow/ask/deny + additionalContext）：`/Users/rad/.zcode/cli/plugins/cache/zcode-plugins-official/zcode-guide/0.3.0/skills/diagnosing-hooks/SKILL.md`
14. AIsa skill 工作流与 quote 纪律（"Quote does not execute and is not approval to execute"/"Re-quote if tools, arguments, or scope change"）：`/Users/rad/.agents/skills/aisa/SKILL.md`
15. CoinCall 现状代码：`coincall-core/app/modules/decision.py`（三信号引擎与证据包）、`coincall-sdk/tools/mcp_server.py`（五工具与 isError 人话转发）、`coincall-gateway/app/core/schemes.py`（402 质询结构）、`coincall-sdk/coincall/policy.py`（L0 check/白名单）、`coincall-sdk/skills/coincall-consumer/SKILL.md`（先 quote 后 call 纪律）、`coincall-console/src/pages/Overview.tsx`（人看板消费 /decision/services）。
