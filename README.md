# CoinCall — AI Agent Integration Platform on BOT Chain

**Pay-per-Request · 单一 API Key · 链上结算 · 失败不扣款**

CoinCall 是运行在 BOT Chain 上的 AI Agent 按次付费服务层：Agent 用一个 API Key 接入平台全部服务，按次付费；平台为 Agent 提供服务方的履约历史、安全评估、内容扫描等综合决策建议；服务方身份注册（ERC-8004）与支付托管（PayVault 合约）全程链上可核验。

## 核心能力

| # | 能力 | 说明 |
|---|---|---|
| 01 | **AI Agents 一把钥匙** | Single API Key, Pay-per-Request——一个 Key 接入全部服务，无需逐服务注册议价 |
| 02 | **综合决策建议** | 履约历史（成功率/p95/调用量）、安全评估（链上身份/收款绑定/内容扫描）、收入与新鲜度信号，报价内嵌 advice |
| 03 | **链上信任底座** | BOT Chain 生态：ERC-8004 身份注册、PayVault 支付托管、Charged 事件为唯一计费真相 |
| 04 | **失败不扣款** | 后付费结算：Provider 未履约不入结算队列；收据 Ed25519 签名可离线验证 |

## 架构与仓库结构

```
┌──────────────┐   ┌──────────────┐
│  Consumer    │   │  Provider    │
│  Agent/CLI   │   │  (上游服务)   │
└──────┬───────┘   └──────┬───────┘
       │ MCP / SDK        │ 凭证加密托管
       ▼                  ▼
┌─────────────────────────────────────┐
│  coincall-gateway :8030（数据面）     │  X-PAYMENT 验证 → 中转 → 结算队列
│  链下签名·幂等·影子闸·内容扫描·收据    │
└──────┬───────────────┬──────────────┘
       │               │
       ▼               ▼
┌─────────────┐  ┌──────────────────┐
│ coincall-core│  │ coincall-         │
│ :8020 管理面 │  │ bot-chain-api     │
│ 目录/决策/探测│  │ :8010 链适配/keystore│
└─────────────┘  └────────┬─────────┘
                          ▼
                 BOT Chain（968 测试网 / 677 主网）
                 PayVault · IdentityRegistry · USDT
```

| 仓库 | 角色 | 测试 |
|---|---|---|
| `coincall-core` | 管理面：目录/manifest、API key、决策引擎（advice）、安全探测与内容扫描、团队与凭证 | 183 unit |
| `coincall-gateway` | 数据面：付费调用中转、X-PAYMENT 验证、keeper 批结算、收据签名、对账监控 | 201 unit |
| `coincall-bot-chain-api` | 链适配器：RPC/合约调用、keystore 代管签名、事件索引 | 134 unit |
| `coincall-console` | 控制台（React）：落地页、Provider/Consumer 工作台、决策视图 | 178 vitest |
| `coincall-sdk` | 消费端 SDK + MCP server（五工具）+ L0 预算护栏 + skill | 86 unit |
| `coincall-contracts` | PayVault / MockUSDT 合约、部署脚本（双网络）、EIP-712 黄金向量 | 61 |
| `coincall-examples` | consumer_agent v1/v2（真实 LLM+MCP 演示）、agent_loop | — |
| `coincall-docs` | 设计文档、审计报告、主网调研、runbook | — |

## 快速开始（测试网）

```bash
./up.sh status    # 四服务健康检查（8010/8020/8030/5173）
./up.sh start     # 一键拉起（keeper 5s 批结算，core 带外网代理）
```

浏览器打开 `http://127.0.0.1:5173/#/welcome` → 连接钱包 → 选择身份。

消费端 Agent 接入（详见 `coincall-docs/agent-onboarding.md`）：

```bash
# MCP 一条命令挂载（五工具：catalog / service_quote / paid_service_call / spend_report / wallet_status）
uvx --from coincall-sdk coincall-mcp

# 或跑分层宿主演示（真实 LLM + MCP + 链上付费）
cd coincall-examples && uv run --project ../coincall-sdk python consumer_agent_v2.py \
  "帮我查询 binance 期货中目前涨得最猛的交易对有哪些？"
```

## 付费流程（后付费模型）

```
Agent → service_quote（报价+平台建议+安全评估，免费）
      → paid_service_call：本地 EIP-712 签名支付授权 → X-PAYMENT 头 → 网关
网关 → 验签/链上约束/幂等/影子闸 → 中转上游（凭证注入，目录脱敏）
     → 2xx 才入结算队列 → keeper 批量 chargeWithSigBatch 上链（Charged 事件）
     → Ed25519 签名收据回传（intent→receipt→onchain 全链路可对账）
失败（非 2xx/超时/内容拦截）= 不入队 = 不扣款
```

护栏纵深：SDK L0 三重预算+白名单（本地） → 网关钱包日限+影子闸（平台） → 链上 Charged 唯一真相（对账）。

## 安全

- 秘密纪律：`.env`/keystore/DuckDB/日志全量不入根仓（白名单 .gitignore + 双硬门核验）
- 合约审计：`coincall-docs/audit/payvault-security-audit.md`（0 High / 1 Medium，条件放行，三项缓解已落地）
- 内容安全：16 条确定性规则（泄露/投毒）探测时扫描 + advice 安全评估如实披露边界
- 对账监控：`GET /internal/keeper/reconcile`（Charged ↔ settle_queue 四元组比对）

## 主网（BOT Chain 677）

调研与切换 runbook：`coincall-docs/research/mainnet-readiness.md`（含 §0 红线：**主网模式下 AI 助手不得自主发起任何链上写操作，一切上链由发起人手动执行**）。四仓已参数化（env/VITE/网络表），部署走 `mainnet-deploy-keystore.sh`（keystore 直署 + 零地址断言 + 双重确认门）。

## 根仓结构说明

本仓库是平台的**版本钉子与分发清单**：`.gitignore` 白名单法只纳管 `coincall-*/` 目录，其余（脚本、报告、本地配置、数据）一概不入仓。

- 七个 `coincall-*` 子仓均为**独立 git 仓库**，以 gitlink（mode 160000）钉住各自 HEAD——只钉版本，内容不入根仓；`coincall-docs` 非 git，以普通文件整体纳管
- 各子仓独立演进、各自提交，并各自维护 `.gitignore` 与秘密防护；根仓层面额外全量排除 `.env`（保留 `.env.example`）、keystore、DuckDB 数据、日志与缓存
- 升级子仓版本：先在子仓内提交，再回根仓 `git add <子目录>` 更新 gitlink 指针后提交

## License

本仓库与 coincall-* 子仓适用 **BSL 1.1**（评估/研究/教学/黑客松/演示/自用放行；2030-10-08 起转 GPL-2.0+）。根仓参数见 [LICENSE](LICENSE)，版权与 TODO 见 [NOTICE](NOTICE)；各子仓建议各放同名副本（见 NOTICE TODO-3）。
