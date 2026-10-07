# Consumer Agent 接入设计：MCP 服务 + Skill（2026-10-07 定稿）

> 命题（发起人）：试用调试完成后，如何把 Skill 和 MCP 服务给 Consumer 的 Agent 使用——Agent 持本地钱包私钥，需要监督与细粒度授权防浪费（安全部分见 agent-wallet-trust.md，L0 已交付）。
> 本文回答"接口怎么给"：三种接入形态 + 控制台导出流 + MCP 工具面扩展。

## 1. 三种接入形态（按 Agent 宿主能力分档）

| 形态 | 适用 Agent | 交付物 | 交互边界 |
|---|---|---|---|
| **A. MCP Server**（主路径） | 任何 MCP 客户端（Claude/ZCode/Cursor/自研） | `coincall-mcp` stdio server（`uvx coincall-sdk` 即起） | 工具调用即付费调用；预算/白名单由 env 注入 L0 引擎 |
| **B. Python SDK**（编程路径） | LangChain/自研 Agent 进程内 | `from coincall import Client`（pip 安装） | 代码级完全控制；policy 参数显式传 |
| **C. Skill**（指导层） | 有 skill 机制的宿主（如 ZCode/Claude Skills） | `coincall-consumer` skill（SKILL.md + 脚本） | 教 Agent **如何安全地**用 A/B：何时查目录、何时报价、何时需要人确认、预算纪律 |

关键设计判断：**Skill 不重新实现支付**——它包装 A/B 并注入"使用纪律"（先 catalog→看价→确认预算→再调；绝不循环重试付费调用；超限即停并报告）。这与 AIsa skill 的 `search→schema→quote→call` 工作流同构（我们天然有 quote=目录定价）。

## 2. MCP 工具面（v2：从 2 工具扩到 5）

现有：`catalog` / `paid_service_call`。新增三个只读工具补全 Agent 决策闭环：

```
catalog          列服务+定价+schema（免费）
service_quote    单服务报价详情：价格/收款方/自己的余额与授权额度/预算余量（免费）
                 —— Agent 花钱前"看价"，L0 答预算余量，天然 quote 语义
paid_service_call 付费调用（唯一花钱工具；被 L0 策略引擎包裹）
spend_report     本地账本聚合：已花/剩余/最近 N 笔（intent→receipt→onchain 状态）
                 —— Agent 可自查+向用户汇报，也是 T5 对账出口
wallet_status    钱包健康：地址/链/USDT 余额/对 PayVault 授权额度/L0 各限额现值
                 —— 挂载后第一件事自查，缺什么立刻告诉用户去补
```

工具描述里写死使用纪律（Agent 读 description 学规矩）：
- paid_service_call 的 description："**付费动作**。调用前先 catalog 确认 service_id 与定价，用 service_quote 核对余额/预算。失败会返回人话指引；**不要自动重试付费调用**（可能是恶意循环）——把指引转告用户。"

## 3. 控制台"导出给 Agent"流（闭环最后一公里）

Consumer 工作台新增「给我的 Agent 接入」卡片（试用调通后出现）：
1. 三个 Tab 对应 A/B/C 形态，各给**可复制的完整配置**：
   - MCP Tab：`uvx --from coincall-sdk coincall-mcp` 一行 + env 清单（API_KEY 预填、GATEWAY_URL=当前 origin+/api/gw、WALLET_KEY 指向 0600 文件路径而非明文、L0 五变量带当前控制台同款默认值）；「复制 MCP 配置」按钮（JSON 格式适配主流客户端 mcpServers 片段）；
   - SDK Tab：五分钟 Python 示例（Client+PolicyConfig 即 L0 全开）；
   - Skill Tab：安装命令 + SKILL.md 链接；
2. **安全自检清单**（勾选后才能复制）：明文 key 不进代码库/私钥文件 0600/预算三顶已设/白名单已配——把劝退项做成引导完成项；
3. 导出配置里**绝不包含私钥明文**（只有路径引用），页面上明示。

## 4. Skill 设计（coincall-consumer）

```
skills/coincall-consumer/
├── SKILL.md      frontmatter(name/description/when_to_use) + 使用纪律正文
├── scripts/
│   ├── setup.sh  交互式：生成钱包(或导入)→mint/approve 指引→写 .env
│   └── call.py   catalog/quote/call/report 四命令的 CLI 薄壳（调 SDK）
└── references/
    └── security.md  五问五答（劝退项自查清单，来自 agent-wallet-trust §3）
```

SKILL.md 纪律核心（写给 Agent 的操作契约）：
- 触发：用户要"调用/购买/使用 CoinCall 上的付费服务"时；不触发：本地免费工具能做的；
- 流程：`wallet_status 自查 → catalog 找服务 → service_quote 看价 → （价格超单笔限额或服务不在白名单 → **询问用户**）→ paid_service_call → spend_report 汇报`；
- 铁律：付费调用失败不自动重试；预算触底立即停止并报告剩余；每笔付费调用都要能报出"买了什么/花了多少/收据号"；
- 密钥纪律：永不打印/上传私钥与 api key。

## 5. 交付切分

| 步骤 | 仓库 | 量级 |
|---|---|---|
| ① MCP 工具面 2→5（service_quote/spend_report/wallet_status）+ 工具纪律描述 | coincall-sdk | 0.5d |
| ② 控制台「给我的 Agent 接入」卡（三 Tab 配置生成+安全自检清单） | coincall-console | 0.5d |
| ③ coincall-consumer skill（SKILL.md+setup.sh+call.py） | coincall-sdk/skills/ | 0.5d |
| ④ 真机验收：MCP 挂 ZCode/Claude 走通付费调用+预算拒付演示 | — | 0.5d |
