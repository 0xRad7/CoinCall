# Agent 持钥信任设计：威胁模型、方案盘点与 CoinCall 分层适配（调研报告）

> 命题（发起人原话浓缩）：消费者用控制台完成试用调试后，为他的 Agent 提供 Skill 与 MCP 服务，让 Agent 安全地使用配置好的钱包。Agent 需持有本地钱包私钥，必须设计监督与细粒度授权机制防止 Agent 浪费资金；是否需要审计？信任设计不好，用户侧就是劝退项。痛点是安全性，尽管 Agent 本身不是平台责任。
>
> 调研时点：2026-10-01。方法：WebSearch（英文关键词）+ WebFetch 读原文 + 项目内文档/代码核对（`coincall-docs/00~04`、`coincall-sdk/coincall/{wallet,client}.py`、`coincall-sdk/tools/mcp_server.py`、`coincall-gateway/app/modules/call_route.py`、`BOT_CHAIN_REPORT.md`）。每处来源见 §2/§6 链接；查不到的如实标注。

---

## 1. 威胁模型（先立靶）

前提：消费者按 03 §3 生成**专用付费 EOA**（私钥本地 0600 文件/env，永不离开消费者机器），Agent 通过 MCP/Skill 调 `paid_service_call`。资金损失向量五条，逐条标注"现有哪层挡 + 缺口"。

### T1 失控循环（bug 导致重复调用刷爆）

- **现有挡层**：
  - SDK 本地预算计数器（`Client.budget_raw` + `_spent_raw`，超限拒发请求）——但**仅内存态、进程重启即清零、且可选**（03 §6）；
  - 自动幂等键 = `sha256(service_id + canonical params)`（03 §5 ③）——**字面重试**（同参数）会被网关幂等挡住不双扣；但**参数带变化量**（时间戳/计数器）的循环完全绕过；
  - 网关影子闸门在途笔数 < K=3（00 §3）——限并发不限串行速率；
  - api key `quota_raw` 单笔上限（`call_route.py` L170：`price > quota → 402 quota_exceeded`）——限单笔不限次数。
- **缺口**：无日额、无速率限制、预算不持久化 → 一次 while-true 循环（参数变化型）可把钱包余额+approve 额度全部耗尽。**这是五条中最现实的一条。**

### T2 金额错误（prompt 注入把 0.01 改成全余额）

- **现有挡层（结构性优势）**：CoinCall 的金额**不由 Agent 输入**——`Client.call()` 从目录 manifest 取 `pricing.amount_raw` 组装 Authorization（`client.py` L129），网关 ⑤a 再验"金额==定价"（02 §3），PayVault 合约再验 `ecrecover==from` 且 `auth.to==本合约`（04 §2）。注入无法把 value 改成任意数；它能做的最坏只是"反复调用贵的服务"（退化为 T1）。
- **缺口**：若未来引入 `upto` 型按用量计费（x402 v2 语义，见 §2.1），单笔上限语义需要 SDK 侧再卡一道。

### T3 目标劫持（注入诱导 approve/transfer 给攻击者）

- **现有挡层**：
  - 支付授权 `to` 锁死 PayVault（SDK 硬编码 `wallet.pay_vault`；合约二次强制）——签名授权**只能**用于向 PayVault 付服务费，不能指向任意地址；
  - MCP 工具面**只有** `catalog` + `paid_service_call` 两个工具（`mcp_server.py` TOOLS_SPEC）——Agent 的自然语言层没有任何"转账/approve"可调用的工具；
  - SDK 侧 approve 只指向 PayVault（`approve_vault`，03 §3 ③）。
- **缺口**：私钥在进程内，**进程内任意代码执行**（含被注入的 Agent 逻辑通过其它工具执行 shell）仍可签任意交易。此缺口本地策略引擎只能缓解（见 T4），链上会话密钥（L2）才能根治。

### T4 密钥外泄（进程被读、日志打印、供应链投毒）

- **现有挡层**：A1 纪律——私钥只进 env/0600 文件与内存，任何 HTTP 头/JSON/calldata 不携带；`LocalWallet.__repr__` 不泄露；私钥文件权限强制 0600；`_send_signed` 前断言 chainId（防错链）（`wallet.py`）。
- **缺口（诚实边界）**：本地 EOA 对"进程完整沦陷"无解——内存可读、恶意依赖可读 env。可做的是**把爆炸半径做成有限**：专用钱包只放小额（最坏损失=钱包余额与 approve 额度的较小者），永不导入主钱包私钥。根治靠 TEE/MPC（Turnkey/Privy/Lit，§2.5），但那引入"信任第三方基础设施"的新信任项，与"资金永不过平台手"（铁律 P7）的平台叙事冲突，只作为对照组。

### T5 不可追溯（花了钱没人知道花哪了）

- **现有挡层**：网关收据三件套（X-Receipt-Id/X-Charged-Raw/X-Receipt-Sig，02 §4）+ 平台 calls 流水（可查但归平台）+ 链上 Charged 事件（批量延迟上链，04 §2）。
- **缺口**：**消费者本地无持久化账本**——SDK 只有内存计数器。事后对账（本地意图 ↔ 平台收据 ↔ 链上事件）缺一环。

### 威胁×挡层总表

| 威胁 | 现有层（CoinCall 已有） | 缺口 | L0 补 | L1 补 | L2 根治 |
|---|---|---|---|---|---|
| T1 失控循环 | 可选内存总额预算；同参幂等；影子闸门并发≤3；quota 单笔上限 | 无日额/速率；预算不持久 | ✅ | 部分（大额人审减速） | ✅ 合约级 rate limit |
| T2 金额错误 | 金额=目录定价三处强校验；Agent 不输入金额 | 未来 upto 计费需单笔帽 | ✅ 单笔帽冗余层 | ✅ 大额人审 | ✅ valueLimit |
| T3 目标劫持 | auth.to 锁 PayVault；MCP 无转账工具 | 进程内任意签名 | ✅ 服务白名单（限面不减限深度） | ✅ 新目标人审 | ✅ 合约级 allowlist |
| T4 密钥外泄 | 0600/env 纪律；专用隔离钱包 | 进程沦陷无解 | 缓解（限额使窃取收益=余额上限） | ✅ 转走余额需过人审 | TEE/MPC 才根治 |
| T5 不可追溯 | 平台收据+流水+链上事件 | 本地无账本 | ✅ 只追加本地账本 | — | — |

---

## 2. 现有方案盘点（2025–2026 公开资料）

### 2.1 x402 生态的客户端限额

- **机制一句话**：x402 的服务端 402 质询里 `maxAmountRequired` 表达"单次支付要求"（v1 exact 方案=精确金额；v2/upto 方案=授权上限、按实际用量 settle）；**累计限额不是协议字段，而是客户端策略**——官方 MCP 客户端默认只允许白名单资产 + **$1 USD 累计消费帽**，可用 `spendControls` 覆盖（调高/关闭 `maxAmountPerPayment`、`allowedAssets` 扩资产、`spendControls:false` 全关），并可在钱包签名前插自定义 `policies` 过滤支付要求、`onPaymentRequested` 钩子拒绝签名。
- **挡哪些威胁**：T1（默认累计帽+可加策略）、T2（`maxAmountPerPayment` 单笔帽）、T3（`allowedAssets` 资产面收窄）。
- **对 CoinCall 适配成本**：**低**。我们的 X-PAYMENT/402 质询本就按 x402 形状对齐（铁律 P9）；把 SDK 的 `budget_raw` 扩成 `spendControls` 三元组（总额/单笔/资产=仅 USDT+仅 PayVault）即是同构实现。exact/upto 语义差异在 payment_scheme 层已预留多态位（00 §4）。
- 来源：[x402 docs — MCP Server with x402](https://docs.x402.org/guides/mcp-server-with-x402.md)（spendControls/$1 默认帽/policies/onPaymentRequested 原文）；[exact 方案](https://docs.x402.org/schemes/exact.md)；[upto 方案](https://docs.x402.org/schemes/upto.md)（"authorize max, settle actual usage"，EVM 走 Permit2 绑定 facilitator 地址）；[文档索引](https://docs.x402.org/llms.txt)。另：社区有 2026-02 的 middleware 级限额 feature request（搜索发现，精确 issue URL 未定位到，标注"经搜索发现"）。

### 2.2 Safe 权限模块（Allowance/Spending Limits）

- **机制一句话**：Safe 智能账户上启用 **Allowance 模块**，主签者 `addDelegate(agent地址)` + `setAllowance(delegate, token, 金额, resetTime分钟, resetBase)` 授予"每时间窗最多花 X"的循环额度；Agent 私钥只能对 `generateTransferHash(safe, token, to, amount, …, nonce)` 签名并走 `executeAllowanceTransfer`——超剩余额度的转账在**合约层**直接失败，窗口到期自动重置。
- **挡哪些威胁**：T1（时间窗循环额度）、T3（Agent 仅是 delegate，不是 owner，动不了主资产）、T4（被偷的只是受限 delegate 键）。
- **对 CoinCall 适配成本**：**中高**。BOT Chain 无官方 Safe 部署，需自行部署 Safe+Allowance 模块（一套已审计合约但要自行部署维护），且 Safe 的 Agent 体验走模块批交易而非我们的 X-PAYMENT 热路径——形态不匹配，**借鉴其"时间窗循环额度"参数设计**（`resetTime`/`resetBase`）进 L0 的日额实现即可。
- 来源：[Safe docs — AI agent quickstart: agent with spending limit](https://docs.safe.global/home/ai-agent-quickstarts/agent-with-spending-limit)（setAllowance 参数、executeAllowanceTransfer 流程原文）；[Safe Help Center — Set up and use Spending Limits](https://help.safe.global)（UI 侧说明）。注：任务书提及的 "Safe{Pass}" 经多轮检索**未找到该命名产品**（Safe 产品族为 Safe{Core}/Safe{Wallet}/Safe{SDK}），已按实际的 Allowance/Spending Limits 模块盘点。

### 2.3 ERC-4337 Session Keys（ZeroDev / Alchemy）

- **机制一句话**：主密钥对策略签名安装一个受限会话验证器模块，会话密钥只能在策略内发 UserOp——ZeroDev（Kernel v3，@zerodev/permissions）：**Call Policy**（target 地址 allowlist + `valueLimit` 每笔值上限 + ABI 级函数选择器 allowlist + 参数条件 `ParamCondition.EQUAL/GREATER_THAN/…`）、**Rate Limit**（`count`+`interval`，带 reset 版=循环配额）、**Gas Policy**（总 gas 帽）、**Timestamp Policy**（起止窗）、sudo 全放行；Alchemy（Wallets API `wallet_createSession`/`grantPermissions`）：`contract-access`/`functions-on-contract`（地址+选择器）、`erc20-token-transfer`（`allowance` 累计帽）、`gas-limit`、`expirySec`、危险旁路 `root`。
- **挡哪些威胁**：T1（rate limit/时间窗）、T2（valueLimit/allowance）、T3（目标 allowlist 到函数级）、T4（会话密钥泄露≠主钥泄露，可吊销）——**链上强制，进程沦陷也绕不过**。
- **对 CoinCall 适配成本**：**高（现状下）**。BOT Chain 测试网 EntryPoint v0.7 已部署且为以太坊同款单例（BOT_CHAIN_REPORT），但**项目实测结论：bundler 不可靠**，且官方 Agent Wallet REST 不可达（解析内网 IP）。可行变通：EntryPoint 的 `handleOps` 任何人可调——可不经 bundler，由 SDK/keeper 经 coincall-bot-chain-api `/contracts/send` 直提 UserOp（本链 gas 恒定 20gwei，估算简单）；但账户合约+会话验证器模块需自写自部署自审计。**归入 L2 roadmap，不作 V1 依赖。**
- 来源：[ZeroDev — Call Policy](https://docs.zerodev.app/smart-accounts/permissions/policies/call)（toCallPolicy 字段原文）；[ZeroDev — Rate Limit Policy](https://docs.zerodev.app/smart-accounts/permissions/policies/rate-limit)；[ZeroDev — Permissions/Session Keys 总览](https://docs.zerodev.app/smart-accounts/permissions/intro)；[zerodev-examples — transaction-automation.ts](https://github.com/zerodevapp/zerodev-examples)；[Alchemy — Session Keys (Wallets API)](https://www.alchemy.com/docs/wallets/reference/wallet-apis-session-keys)（权限类型字段原文）。

### 2.4 MCP 钱包实践（Coinbase 系）

- **机制一句话**：Coinbase AgentKit 的钱包供给分两型——`EthAccountWalletProvider`（**本地私钥**走 env）与 CDP Server/Smart Wallet（**API key + wallet secret 调远端 enclave 钱包**，密钥不出 Coinbase TEE）；新推的 `@coinbase/payments-mcp` 是 MCP 服务器+伴生钱包 App，把钱包/onramp/x402 支付合一。**关键安全事实**：AgentKit 官方 README 明文承认 **"AgentKit does not gate transfers behind human approval, enforce spend caps, or allowlist destinations"**（不人审、不限额、不白名单，风险自负），建议用 LangChain guardrails 中间件自建。
- **挡哪些威胁**：Coinbase 官方 MCP 本体对 T1–T3 基本不设防（诚实披露）；防护靠 CDP enclave 挡 T4 的一半（密钥不出 TEE）+ 开发者自建策略。
- **对 CoinCall 适配成本**：**不适用但极有叙事价值**——CoinCall 的 `paid_service_call` 工具面（无转账工具+金额目录锁定+默认预算）恰好补上 AgentKit 公开承认缺失的三件事（人审可关、限额内建、白名单内建），这是对标的"劝退项"反转点。
- 来源：[coinbase/agentkit README — Managing Risk](https://github.com/coinbase/agentkit)（免责声明原文，经 raw.githubusercontent 核对）；[coinbase/payments-mcp](https://github.com/coinbase/payments-mcp)（安装形态：`~/.payments-mcp/bundle.js`，无明文密钥入 MCP 配置）；[CDP docs](https://docs.cdp.coinbase.com)（wallet secret 语义）。注：任务书所指旧名 "coinbase/coinbase-mcp" 仓库已不存在（404），现役为 agentkit 与 payments-mcp，已如实更正。

### 2.5 托管型策略执行（Turnkey / Privy / Lit）——"Agent 不持裸私钥"对照组

- **Turnkey**：私钥封存于自建 TEE 集群，**每个 API 请求先过策略引擎再进 enclave 签名**；策略为 JSON：`effect: EFFECT_ALLOW|EFFECT_DENY` + `consensus`（谁能发起，可表达 quorum/多人批准）+ `condition`（如 `eth.tx.to == '<ALLOWED_ADDRESS>'`），**显式拒绝优先、默认隐式拒绝**、root quorum 旁路。挡 T1–T4 全部（策略在密钥旁强制执行）。来源：[Turnkey docs — Policies overview](https://docs.turnkey.com/features/policies/overview.md)、[Secure enclaves](https://docs.turnkey.com/security/secure-enclaves.md)、[Turnkey whitepaper](https://www.turnkey.com)（2025-02，可验证计算）。
- **Privy**：策略=policy→rules→conditions，**默认拒绝、DENY 优先**；可表达转账上限、收款人/合约/网络 allowlist-denylist、calldata 解码级参数约束（`ethereum_calldata`+ABI 解出 `function_name._to/_value`）、**EIP-712 typed data 域/消息级约束**（`ethereum_typed_data_domain/message`）、**状态ful 累计限额**（`reference` 源按时间窗累计交易额）；策略在 **TEE 内先于签名/导出执行**。EIP-712 级约束与我们 X-PAYMENT 形态天然对口。来源：[Privy docs — Controls and policies overview](https://docs.privy.io/controls/policies/overview.md)（字段原文）。
- **Lit Actions**：签名策略=一段 JavaScript（Lit Action）在分布式节点网络（MPC/TEE 混合）执行，条件满足才拼出签名（PKP）；官方 agent-wallet 提供 "Set Tool Policy"（限代币金额/地址/参数）。挡 T1–T4；代价是信任 Lit 网络与策略代码本身。来源：[LIT-Protocol/agent-wallet](https://github.com/LIT-Protocol/agent-wallet)、[Writing wallet signing policies as code](https://spark.litprotocol.com/signing-policies-as-code/)、[Lit Actions SDK docs](https://developer.litprotocol.com)。
- **对 CoinCall 适配成本**：**不采用（V1）**。三者均要求把消费者密钥/签名权交第三方基础设施，与铁律 P7（资金永不过平台手）和"平台不碰私钥"的信任主张直接冲突，且 BOT Chain（非主流 EVM 链）不在其支持列表。**价值在范式**：它们的"默认拒绝+DENY 优先+签名前策略评估+状态ful 累计"四原则应原样搬进 L0 策略引擎的设计。

### 2.6 审计/可观测（本地账本 / 预演模拟 / 告警）

- **交易前模拟**：Tenderly Simulation API 在签名/广播前模拟单笔或捆绑交易，返回 revert 检测、gas 估算、**资产余额变化明细**——行业通用 guardrail 形态是"构建交易→模拟→策略引擎→才签名"。对 CoinCall：V1 热路径是**签名授权而非链上交易**，模拟对象不存在；仅 approve/mint 等本地直签交易可用 eth_call/自建 dry-run 替代（coincall-bot-chain-api 已有 dry_run 部署预览先例）。来源：[Tenderly docs — Simulations overview](https://docs.tenderly.co/simulations/overview)。
- **本地 spend ledger**：x402 MCP 官方文档把"per-user/per-agent/per-session spend limits"列为策略层应实现项（见 §2.1 引文）；社区实践（WAIaaS/MoonPay agent 钱包等）收敛于"限额+预演+审计流水"三件套（搜索综合，未逐一核原文）。**未找到任何"本地只追加账本"的标准化开源格式**——如实标注：这是各家自研件，CoinCall 自定 JSONL 即是业界常态。
- **告警**：桌面通知/控制台待审队列无公开标准，属产品自研件。

---

## 3. CoinCall 适配设计建议（分层）

### L0 — 纯本地策略引擎（hackathon 可交付，落在 coincall-sdk）

一个 `PolicyEngine`（≤150 行，SDK 内嵌，MCP server 装配时启用），五件套：

1. **硬预算三顶**：`total_budget_raw`（总额，进程生命周期）+ `daily_budget_raw`（日额，UTC 日界，借 Safe `resetTime` 语义）+ `max_per_call_raw`（单笔帽，x402 `maxAmountPerPayment` 语义）。计数器**落盘**（`~/.coincall/spend_state.json`，0600，写后原子 rename）解决重启清零；任一触顶→拒发请求并返回人话（"预算触底，剩 N；调 COINCALL_BUDGET_RAW 或明日再试"）。
2. **目标 allowlist**：`allowed_service_ids`（目录服务白名单，缺省=全部目录内服务）+ 硬编码不变量：支付授权 `to` 恒为 PayVault、approve 恒指向 PayVault、资产恒为 USDT。白名单外服务→拒绝（默认拒绝原则，Turnkey/Privy 范式）。
3. **速率限制**：`min_interval_s`（两次付费调用最小间隔）+ `max_calls_per_hour`。专杀 T1 参数变化型循环（幂等键挡不住的那种）。
4. **审计账本**：`~/.coincall/ledger.jsonl` **只追加**，每行一条 `{ts, intent:{service_id, params_hash, price_raw, idempotency_key}, receipt:{receipt_id, charged_raw, receipt_sig}, onchain:{batch_tx_hash, charged_event_block}}`（链上段由 SDK 拉取 keeper 批交易哈希后回填或标注 `pending`）。三方可对账：本地 ledger ↔ 平台 `/consumer/me/calls` ↔ 链上 Charged 事件。
5. **紧急熔断**：预算触底自动进入只读模式（catalog 可用、paid_service_call 拒绝）；`COINCALL_DISABLE=1` 环境变量或删除 state 文件即手动停机。**止损主权仍在链上**：撤 approve / 转走钱包余额随时可做（P7/P8）——L0 只是更快的那只手。

**对威胁模型的挡位自评**：T1 ✅（三顶+速率+持久化）、T2 ✅（单笔帽为目录价加冗余层；金额本就三处锁定）、T3 ✅（白名单限面；进程沦陷除外→归 T4）、T4 缓解（专用小额钱包使最坏损失=余额上限，诚实声明）、T5 ✅（账本闭环）。**结论：L0 对 T1/T2/T3/T5 足够，对 T4 是"损失有界"而非"不可能"。**

### L1 — 审批门（可选档，赛后再做）

策略引擎加一条规则：`单笔 > approval_threshold_raw` 或 `服务不在已批准集合` → 调用进入 `pending_approval` 态，签名**不发生**；桌面通知（macOS `osascript`/通知中心）或控制台待审队列让人一键批准/拒绝，批准即临时把该服务/金额加入会话白名单。对标 AgentKit guardrails chatbot 的人审中间件（§2.4），但我们的人审在**策略引擎内、签名前**，而非 LLM 层可绕过的提示过滤。成本：SDK 侧 ~100 行 + console 一个队列页。

### L2 — 链上会话密钥（roadmap，简评）

- **基础在**：BOT Chain 测试网 EntryPoint v0.7 已部署（以太坊同款单例 0x0000000071727De22E5E9d8BAf0edAc6f37da032），EVM 全兼容。
- **障碍是事实**：bundler 不可靠（项目实测结论；BOT_CHAIN_REPORT 亦证主网 DNS 污染、官方 Agent Wallet REST 不可达）。
- **可行路径**：不依赖 bundler——会话账户的 UserOp 由 SDK/keeper 组装后经 coincall-bot-chain-api `/contracts/send` 直调 `EntryPoint.handleOps`（任何人可调；本链 gas 恒定价，自估可行）。合约侧自写一个 **SessionKeyValidator**（ZeroDev Call Policy 的极小子集：目标∈{PayVault, 已批准服务端点关联合约} + 每笔 valueLimit + 累计帽 + 时间窗），主钥（EOA）安装后 Agent 日常只用会话钥。
- **定位**：根治 T3/T4 的唯一路径（进程沦陷也偷不到无限制签名权），但**新合约 = 新审计面**，赛时绝不引入；作为 "V2 安全路线图" 一页写进 deck。

### 审计结论：是否需要审计？

分三块回答，不混谈：

| 对象 | 要不要第三方审计 | 理由与叙事 |
|---|---|---|
| **L0 本地策略引擎**（SDK 纯客户端逻辑） | **不需要** | 不上链、不持链上权限，第三方审计在此无意义；正确主张是**全开源可自查**——策略代码 ≤200 行，用户 10 分钟可读完（对照 Turnkey/Privy 的闭源 TEE 策略引擎，这是**透明度优势**而非劣势） |
| **PayVault 合约**（已在资金关键路径上） | **主网/真实资金前需要**；测试网 demo 阶段**披露即可** | 04 §5 已定调"合约未经审计：额度上限由消费者 approve 自主控制 + P8 提现永开 + 演示小额——写进演讲诚实边界"。维持该定调，demo 叙事含此句 |
| **L2 会话密钥合约**（未来） | **必须**，上线真实资金前置条件 | 新智能合约直接管钱，ZeroDev Kernel/Safe 模块均历经多轮审计；自写合约无审计上线=不可辩护 |

**对外信任主张的排序**（回答"怎么说更诚实"）：第一主张用结构事实——"**平台不碰私钥（P7）+ 金额只来自目录定价（三处强校验）+ 授权只能付给 PayVault + 失败从未扣款（后付费时序）**"，这是密码学/时序结构保证，不依赖任何人的诚信；第二主张用透明度——"SDK 与策略引擎全开源，预算/白名单/账本在你机器上，可自查可 fork"；第三主张才是合规姿态——"PayVault 合约测试网阶段未经第三方审计，已内置最坏情形约束（I1–I4 不变量+提现永开），主网化时审计为前置条件"。"我们审计过"（如果没审）比"结构上你不需要信我们"更弱且更危险。**诚实边界本身就是卖点**：对照 AgentKit 官方 README 承认的"不限额/不白名单/不人审"（§2.4），CoinCall 的默认限额+白名单+账本是可演示的差异化。

### 劝退项自查清单（用户视角五问 → 机制映射）

| # | 用户问 | 机制回答 | 层 |
|---|---|---|---|
| 1 | "Agent 最多能花掉我多少钱？" | 总额/日额/单笔三重硬顶，落盘持久，触底自动只读熔断；且钱包本身只放你转入的小额 | L0 |
| 2 | "它会不会把钱转给陌生地址？" | 支付授权收款方锁死 PayVault（合约强制）；服务白名单；MCP 工具面没有转账/approve 工具；大额/新目标可开人审 | 结构+L0(+L1) |
| 3 | "金额会不会被 prompt 注入改大？" | 金额只来自平台目录定价，SDK/网关/合约三处强校验；Agent 的输入里没有金额字段 | 结构 |
| 4 | "钱花哪了我能看到吗？" | 本地只追加账本（意图→收据→链上哈希）+ 平台流水 + 链上 Charged 事件，三方可对账 | L0 |
| 5 | "出问题我能立刻停吗？" | 三级刹车：删 budget/置 COINCALL_DISABLE（秒级）→ 撤 USDT.approve（即时止血，链上主权）→ 吊销 api key（断通道）；失败调用从未扣款 | L0+P7 |

（密钥被偷怎么办——诚实回答："专用钱包最坏损失=你放进去的钱，请像对待现金零钱包一样对待它；这就是我们不接主钱包、不做托管的理由。"）

---

## 4. 与现有代码的落地对照（L0 改动清单，不动协议）

| 改动点 | 文件 | 规模 |
|---|---|---|
| PolicyEngine（三顶预算+白名单+速率+熔断+账本） | `coincall-sdk/coincall/policy.py`（新） | ~150 行 |
| Client.call() 签名前插策略闸；spent 计数改读引擎 | `coincall-sdk/coincall/client.py`（L130 附近） | ~20 行改动 |
| MCP env 扩展：BUDGET_DAILY_RAW / MAX_PER_CALL_RAW / ALLOWED_SERVICES / MIN_INTERVAL_S | `coincall-sdk/tools/mcp_server.py`（ENV 块） | ~15 行 |
| 账本/状态文件 0600 纪律 | 复用 `wallet.py` 的 `_require_key_file_mode` 模式 | ~10 行 |
| console 消费视图加"本地账本导入对照"（可选） | coincall-console | 赛后 |

全部为 SDK 侧客户端改动，不触碰网关/合约/keeper，不违反任何铁律（预算仍由 consumer 侧执行，平台不替用户管预算——03 §6 原则的强化而非背离）。

---

## 5. 来源清单

1. x402 docs — MCP Server with x402：<https://docs.x402.org/guides/mcp-server-with-x402.md>
2. x402 docs — exact 方案：<https://docs.x402.org/schemes/exact.md>
3. x402 docs — upto 方案：<https://docs.x402.org/schemes/upto.md>
4. x402 docs 索引：<https://docs.x402.org/llms.txt>
5. Safe docs — AI agent quickstart (spending limit / Allowance module)：<https://docs.safe.global/home/ai-agent-quickstarts/agent-with-spending-limit>
6. Safe Help Center — Spending Limits：<https://help.safe.global>
7. ZeroDev docs — Permissions (Session Keys)：<https://docs.zerodev.app/smart-accounts/permissions/intro>
8. ZeroDev docs — Call Policy：<https://docs.zerodev.app/smart-accounts/permissions/policies/call>
9. ZeroDev docs — Rate Limit Policy：<https://docs.zerodev.app/smart-accounts/permissions/policies/rate-limit>
10. zerodev-examples — transaction-automation：<https://github.com/zerodevapp/zerodev-examples>
11. Alchemy docs — Session Keys (Wallets API)：<https://www.alchemy.com/docs/wallets/reference/wallet-apis-session-keys>
12. coinbase/agentkit — README（Managing Risk 免责声明）：<https://github.com/coinbase/agentkit>
13. coinbase/payments-mcp：<https://github.com/coinbase/payments-mcp>
14. Coinbase CDP docs：<https://docs.cdp.coinbase.com>
15. Turnkey docs — Policies overview：<https://docs.turnkey.com/features/policies/overview.md>
16. Turnkey docs — Secure enclaves：<https://docs.turnkey.com/security/secure-enclaves.md>
17. Turnkey whitepaper（2025-02）：<https://www.turnkey.com>
18. Privy docs — Controls and policies overview：<https://docs.privy.io/controls/policies/overview.md>
19. LIT-Protocol/agent-wallet：<https://github.com/LIT-Protocol/agent-wallet>
20. Lit Protocol — Signing policies as code：<https://spark.litprotocol.com/signing-policies-as-code/>
21. Lit Actions SDK docs：<https://developer.litprotocol.com>
22. Tenderly docs — Simulations overview：<https://docs.tenderly.co/simulations/overview>
23. ethresear.ch — Key Management for Autonomous AI Agents（2025-01，MPC 对照）：<https://ethresear.ch>（经搜索发现，未核全文）

**未找到/如实标注项**：任务书所指 "Safe{Pass}" 无此命名产品（已按实际 Allowance/Spending Limits 盘点）；旧仓库 coinbase/coinbase-mcp 已 404（现役 agentkit/payments-mcp）；x402 middleware 限额 feature request 的精确 issue URL 未定位；"本地只追加账本"无行业标准格式（各家自研）；botchain.ai 系域名本次未直接抓取（内部 `BOT_CHAIN_REPORT.md` 2026-10-06 实测记录已覆盖该域全部所需事实：EntryPoint v0.7 已部署、主网 DNS 污染、Agent Wallet REST 不可达）。

---

*本报告仅写入 coincall-docs/design/，未改动任何代码。*
