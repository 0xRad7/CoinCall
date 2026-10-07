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

## 环境依赖

| 依赖 | 版本 | 用途 |
|---|---|---|
| macOS / Linux | — | `up.sh` 为 zsh 脚本（lsof/nohup/disown），Windows 请用 WSL |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.11 | 四个 Python 服务的依赖与运行（`uv run` 自动建 venv 并 sync，无需预装 Python；各仓要求 ≥ 3.12） |
| Node.js + npm | ≥ 18（实测 24） | 控制台（Vite 5 + React + ethers）；首次需 `cd coincall-console && npm install` |
| EVM 浏览器钱包 | MetaMask 等 | 控制台连接钱包用；需手动添加 BOT Chain 网络（见下） |

**钱包网络参数**：主网 `chainId 677` · RPC `https://rpc.botchain.ai/` · 浏览器 `https://scan.botchain.ai`；测试网 `chainId 968` · RPC `https://rpc.bohr.life/` · 浏览器 `https://scan.bohr.life`。

**前置条件**：

- 端口空闲：`8010`（bot-chain-api）/ `8020`（core）/ `8030`（gateway）/ `5173`（console）
- 各服务 `.env` 从对应 `.env.example` 复制起改（console `VITE_*` 链参数、gateway `COINCALL_*`、core `COINCALL_CORE_*` 前缀、bot-chain-api `BOT_CHAIN_*`）；`.env` 不入仓
- 直连 `rpc.botchain.ai` 受限（DNS 污染）时需本地 HTTP 代理：`HTTPS_PROXY=http://127.0.0.1:7890`（up.sh 已为 core 注入，其余直连本机不受影响）
- 可选：`consumer_agent` 演示需 DashScope API key；主网 operator 签名依赖 bot-chain-api 托管 keystore（不入仓，克隆副本不含，缺它则仅测试网/只读操作可用）

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

## 主网（BOT Chain 677）—— 已切换 · 2026-10-08

### 当前合约信息（唯一事实源：`coincall-contracts/deployments/mainnet-677.json`）

| 项 | 值 |
|---|---|
| **PayVault** | [`0x39f9c91992BAfd1528ef87aFDf8B17b7b6cc1818`](https://scan.botchain.ai/address/0x39f9c91992BAfd1528ef87aFDf8B17b7b6cc1818) |
| 计价 USDT | `0xaBabc7Ddc03e501d190C676BF3d92ef0e6e87a3C`（链上 `vault.token()` 核验一致） |
| operator | `0xb1ea3EA94e2Fd7Cb9244cD460dA863FC4b61033A`（bot-chain-api keystore 托管，私钥未出库） |
| IdentityRegistry | `0xB43Edfb9C7609cF645e932B2fF20f26F0d4488dE`（官方既有） |
| 部署交易 | [`0xed7c…29f2`](https://scan.botchain.ai/tx/0xed7cf89878fcf94da650995ce22d2bb67c51a174b5c747900016b0b10e3929f2) · 块 25878130 · gas 805,318 @20gwei（0.0161 BOT） |
| DOMAIN_SEPARATOR | `0xdb23b412a34364764336b28e1b07adae8195809fa94aaa19f881aef834b7231c`（链上实读） |
| 冒烟 | `pending-manual`（红线见下；人工最小冒烟：无签名单笔 chargeWithSigBatch 应 bad_signature 拒绝 + providerWithdraw 权限） |

### 部署方式（keystore 直署，已发生过的流程存档）

```bash
# 前置：bot-chain-api .env 切主网（BOT_CHAIN_NETWORK=mainnet + BOT_CHAIN_ALLOW_MAINNET=1 + PROXY=http://127.0.0.1:7890）并重启
./mainnet-deploy-keystore.sh   # 根目录执行：只读核验→F-02非零断言→dry-run预览→yes确认→keystore签名部署
# 产物：coincall-contracts/deployments/mainnet-677.json（构造参数/DOMAIN_SEPARATOR 链上核验后归档）
```

### 四服务切换（env 清单——已生效，回滚=改回测试网值重启）

| 服务 | 关键 env（值见上表） |
|---|---|
| bot-chain-api :8010 | `BOT_CHAIN_NETWORK=mainnet` + `BOT_CHAIN_ALLOW_MAINNET=1` + `PROXY`（主网模式自动拒载裸私钥，签名走 keystore） |
| gateway :8030 | `COINCALL_PAY_VAULT_ADDRESS` / `COINCALL_PAYMENT_TOKEN_ADDRESS` / `COINCALL_CHAIN_ID=677` / `COINCALL_CHAIN_RPC_URL` / `COINCALL_KEEPER_OPERATOR_ADDRESS` / `COINCALL_KEEPER_CHAIN_MODE=api`（结算经 8010 keystore 签名，规避 DNS 污染） / `COINCALL_IDENTITY_REGISTRY_ADDRESS` |
| core :8020 | **前缀是 `COINCALL_CORE_`**（≠gateway 前缀，易踩坑）：`_PAY_VAULT_ADDRESS` / `_PAY_VAULT_DEPLOY_BLOCK=25878130`（Charged 索引起点） / `_PLATFORM_CUSTODIAN_ADDRESS`；出网探测需 `HTTPS_PROXY` + `NO_PROXY=127.0.0.1,localhost` |
| console :5173 | `VITE_CHAIN_ID=677` / `VITE_CHAIN_NAME` / `VITE_RPC_URL` / `VITE_EXPLORER_URL=scan.botchain.ai` / `VITE_PAY_VAULT` / `VITE_USDT`（改后重启 dev + 浏览器硬刷新） |
| SDK / Agent | `COINCALL_NETWORK=mainnet`（网络表自动取 677/主网 RPC/主网 USDT） + `LocalWallet(pay_vault=0x39f9…)` 显式传入新金库 |

已知混合状态：core DuckDB 保留测试网历史行（GMV/charged_count 为两网合计，主网新事件从 25878130 起计）；EIP-712 域已变——测试网时代的收据/授权全部失效，消费者需对新金库重新 approve。

### 红线（mainnet-readiness.md §0）

**主网模式下 AI 助手不得自主发起任何链上写操作**（部署/转账/approve/charge/身份注册），一切上链由发起人手动执行并核验；AI 允许：只读链上查询、代码配置准备、事后对账（`GET /internal/keeper/reconcile`）。

## 根仓结构说明

本仓库是平台的**版本钉子与分发清单**：`.gitignore` 白名单法只纳管 `coincall-*/` 目录，其余（脚本、报告、本地配置、数据）一概不入仓。

- 七个 `coincall-*` 子仓已以 **git subtree 全量并入**（历史保留，克隆即得完整代码）；本机各子仓仍是独立 git 仓库，可继续独立提交
- 各子仓独立演进、各自提交，并各自维护 `.gitignore` 与秘密防护；根仓层面额外全量排除 `.env`（保留 `.env.example`）、keystore、DuckDB 数据、日志与缓存
- 升级子仓版本：先在子仓内提交，再回根仓同步——`git subtree pull --prefix=<子目录> <子仓绝对路径> main`（内容一致时可 `--squash` 减噪）

## License

本仓库与 coincall-* 子仓适用 **BSL 1.1**（评估/研究/教学/黑客松/演示/自用放行；2030-10-08 起转 GPL-2.0+）。根仓参数见 [LICENSE](LICENSE)，版权与 TODO 见 [NOTICE](NOTICE)；各子仓建议各放同名副本（见 NOTICE TODO-3）。
