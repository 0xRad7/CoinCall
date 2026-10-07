# 00 — 总览与全局约定（Agent 付费服务层 · v2 终态版）

> v2 变更：本版吸收了 2026-10-05~06 全部需求澄清结论——结算切换为链上 PayVault 合约（x402 模拟）、消费端本地付费钱包、平台级可扩展抽象、官方 Agent OS 对齐策略。旧版中"平台账本/充值托管"相关内容全部作废，以本版为准。

## 1. 一句话定位

**在 BOT Chain 上抢先实现官方路线图上的 AgentPay(x402)**：官方 Agent OS 文档已宣告 AgentPay 就是 x402（实测确认，详见 §2），但至今是一页占位符；我们交付 BOT Chain 上第一个可用的 x402 支付链路——Agent 调 Agent 的付费服务，签名即支付承诺，资金永不过平台手。

## 2. 官方 Agent OS 现状矩阵（2026-10-06 经代理实测 botchain.ai）

| 官方组件 | 文档状态 | 服务状态 | 我们的对策 |
|---|---|---|---|
| Agent Wallet | ✅ 实心（4337 托管钱包，API：创建/转账/deploy/allowlist/user-ops） | ❌ wallet-api.bohr.life 解析 10.x 内网 IP，公网不可达；认证文档为空 | coincall-bot-chain-api 已覆盖同类能力（M2/M5），**端点响应字段向官方命名对齐** |
| Agent Identity | ✅ 实心（注册/wallet-binding/metadata/suspend/by-erc8004） | ❌ identity-api.bohr.life 同上 | 同上（05 端点扩展对齐其形态） |
| **AgentPay (x402)** | ❌ 纯占位（18 处 pending engineering input，无流程/无 API/无 schema） | ❌ 无 | **本文档族的全部工作 = 抢先实现它** |
| MCP Service / Skill Hub | ❌ 占位 / Coming Soon | ❌ | 参考 |

战略叙事：**"Agent OS 全栈中，官方没做完的部分（AgentPay）我们做完了；官方做完的部分（Wallet/Identity），我们有形态对齐的开源自托管替代（coincall-bot-chain-api）"**。

## 3. 运行时序终版（唯一权威）

### 阶段 0 —— 一次性准备

```
Provider：① 注册 ERC-8004 身份（coincall-bot-chain-api）② 绑定 agentWallet（收款锚）
         ③ 发布 ServiceManifest（定价/端点/schema）入服务目录
Consumer：① 签发 api key（配额/限流用）② SDK 本地生成专用付费钱包（全新 EOA，
         私钥永不离开消费者机器）③ 向该钱包转 USDT + approve(PayVault, 限额)
```

### 阶段 1 —— 单次付费调用（热路径，签名在消费者进程内完成）

```
① Consumer SDK（消费者 Agent 进程内）：
   组装支付授权 {from,to,value,validAfter,validBefore,nonce}
   用本地私钥做 EIP-712 签名 → base64 后放 X-PAYMENT 头
   POST /call/{service_id}（X-Api-Key + X-PAYMENT）→ 网关
② 网关本地验签（秒回，不上链）：ecrecover==消费者地址 / deadline / 金额==定价 /
   nonce 未重放 / 链上 approve≥钱包余额≥定价（eth_call 经 coincall-bot-chain-api，可缓存）
   加影子闸门：可用额度 = min(链上约束) − Σ该 key 在途金额，在途笔数 < K(默认3)
   ✗ 不过 → 402 质询（x402 同构 JSON）
   ✓ 全过 → 放行
③ 网关转发 Provider 端点（此刻支付已获密码学保证）
④ Provider 2xx → 结果透传 Consumer；{签名授权} 入 settle 队列
   Provider 失败/超时 → 从未扣款、零退款逻辑（后付费时序结构性消除）
```

### 阶段 2 —— 异步结算与提现

```
⑤ keeper（自建 facilitator 的 settle）：攒批 N 笔 → 一笔链上交易
   PayVault.chargeWithSigBatch(...)：合约逐笔 ecrecover+nonce 防重放+
   transferFrom(消费者钱包 → 按 Provider 记账)；gas 由 keeper 赞助
⑥ Provider 随时 withdraw → agentWallet；Consumer 随时调整/撤销 approve（资金主权完整）
```

### 异常路径全表

| 异常 | 位置 | 处置 | 损失边界 |
|---|---|---|---|
| 余额/授权不足 | ② | 402 质询（引导调额） | 0 |
| 签名无效/重放 | ② | 401/409 拒放行 | 0 |
| Provider 挂/超时 | ④ | 不入队不扣款 | 供应方一次计算成本 |
| 撤 approve 竞态 | ②→⑤ | 影子闸门限 K 笔；charge 失败→重试→坏账+拉黑 key | ≤ K×单价 |
| keeper 宕机 | ⑤ | 队列持久化续批；过期笔作废=未扣 | 0 |

## 4. 平台级抽象（可扩展性保证，V1 只实现 BOT Chain）

```
付费网关（平台级）
├── payment_scheme 接口（402 质询/验签/settle 的多态）
│    ├── "erc3009-vault"（V1）：验签对象=自部署 PayVault，settle=keeper chargeWithSig
│    └── "eip3009-native"（未来）：验签对象=带 3009 的代币合约（Base USDC 或官方），
│         settle=keeper 直接提交 transferWithAuthorization——零自建合约
├── ChainAdapter 接口（RPC/签名/余额/交易提交）
│    ├── BOT Chain adapter → 内部全部走 coincall-bot-chain-api HTTP（保持其单链专用，不泛化）
│    └── Base adapter（V1.5 可选，Base Sepolia 最小闭环；V2 完整）
└── 服务目录（manifest 带 chain 字段，按链分列，不做跨链汇总）
```

接口设计纪律：**接口签名以 Base USDC x402 的形态为设计标尺**（那是官方最可能照抄的标准）；所有 x402 语义收口在 payment_scheme 模块，禁止散落网关代码。官方 AgentPay 上线后的迁移：换 scheme 适配器（详见 04 §6 迁移分级）。

## 5. 全局铁律（继承 v1 并增补）

| # | 铁律 |
|---|---|
| P1 | 本服务不直接碰 Web3：一切 BOT Chain 操作经 coincall-bot-chain-api HTTP |
| P2 | 缺端点先补 API：链上动作无既有端点支撑时，先在 05 完成设计再实现消费方 |
| P3 | 默认测试网 968；结算币 USDT（6 位精度），金额统一最小单位整数 |
| P4 | 消费低门槛/供给强身份：Consumer 最小接入=api key+本地钱包；Provider 必须注册 8004 |
| P5 | 我们不是平台不抽佣：Provider 收全额；我们是协议+收单基础设施 |
| P6 | 永久非目标：任务撮合/escrow 裁决/事后验收/LLM-judge/平台佣金 |
| P7（新） | **资金永不过平台手**：平台账户不持有任何消费者/Provider 资金；keeper 只能按合约规则 charge |
| P8（新） | **提现路径永无条件开放**：PayVault 的 providerWithdraw 与消费者的 approve 主权不受任何 admin 锁/pause 限制——合约任何 bug 最坏后果=服务停摆而非资金冻结 |
| P9（新） | 协议形状对齐 x402（402 质询字段名/X-PAYMENT 头/authorization 结构对齐 EIP-3009），链上差异只存在于 payment_scheme 适配层 |

## 6. 优先级总览（细节见 09 任务书）

```
P0（主干，五项）：
  P0-1 coincall-bot-chain-api 三身份端点（绑钱包/注册解析/元数据）
  P0-2 PayVault 支付合约（chargeWithSig/批量/双向提现永开）
  P0-3 新服务地基 + payment_scheme/ChainAdapter 契约冻结
  P0-4 402 付费网关（验签/影子闸门/转发/收据）
  P0-5 keeper 结算器（批量上链/坏账处理）
P1（完整性，四项）：
  P1-1 Consumer SDK 与本地付费钱包（EIP-712 签名/MCP skill）
  P1-2 Provider 接入层（manifest/目录/注册端点，对齐官方形态）
  P1-3 数据与排行榜（流水/收入视图/overview）
  P1-4 演示集成（三幕剧本/内置 demo 服务/彩排）
P2（增强）：Base Sepolia 最小闭环(V1.5)/manifest 上链锚定/商店页/收据上链/官方迁移适配器
```

关键路径：P0-1 → P0-2 → P0-3 → (P0-4 ∥ P0-5) → P1-1 ∥ P1-2 ∥ P1-3 → P1-4

## 7. 术语表（v2 修订项加粗）

| 术语 | 定义 |
|---|---|
| Provider / Consumer | 挂服务的运营方（8004 身份+agentWallet）/ 付费调用方 |
| **X-PAYMENT 头** | 消费者进程内 EIP-712 签名的支付授权（base64 JSON），对齐 x402 PaymentPayload |
| **PayVault** | 自部署支付合约：chargeWithSig 批量结算+Provider 记账提现（04 规格） |
| **keeper** | 自建 facilitator：携签名批量上链结算，gas 赞助方 |
| **影子闸门** | 网关内存级在途额度闸（性能层非资金层），限撤 approve 竞态损失 ≤K×单价 |
| **本地付费钱包** | Consumer SDK 生成的专用 EOA，私钥不出消费者机器 |
| payment_scheme / ChainAdapter | 协议多态与链适配接口（§4） |
| coincall-bot-chain-api | 既有链上网关（唯一链上出口），官方 Agent OS 的开源自托管对齐实现 |

## 8. 仓库形态

- 新服务（付费网关+keeper+SDK）独立目录/仓库，与 coincall-bot-chain-api 纯 HTTP 对接；
- coincall-bot-chain-api 仅做 05 定义的端点扩展（其仓库纪律不变：测试先行/静态门/tag）；
- PayVault 合约源码+编译产物入库（solc 离线编译，运行时零编译依赖）。

## 9. 文档索引（v2）

| 文档 | 主题 | v2 状态 |
|---|---|---|
| 00 | 总览/铁律/时序/抽象/优先级索引 | ✅ 本文件 |
| 01 | 管理面：服务目录与 Manifest | 沿用（manifest 增 chain 字段） |
| 02 | 数据面：402 网关 | ✅ 已修订（X-PAYMENT/影子闸门/scheme 接口） |
| 03 | 消费面：本地钱包与 SDK | ✅ 已修订 |
| 04 | 结算层：PayVault 合约与 keeper | ✅ 已重写（账本方案作废） |
| 05 | coincall-bot-chain-api 端点扩展（对齐官方形态） | 微调 |
| 06 | 数据层 | 沿用 |
| 07 | 演示剧本 | 沿用（第三幕更新为链上结算画面） |
| 08 | 并发构建计划 | ✅ 已重写 |
| **09** | **P0~P1 逐项详细任务书** | ✅ 新增（本文档族的展开视图） |

## 10. 项目命名：CoinCall（2026-10 发起人定名，暂定）

仓库/服务命名规约：统一前缀 `coincall-`，连字符命名，各自独立仓库目录（同级并列）：

| 目录 | 角色 | 对应文档 |
|---|---|---|
| `coincall-core` | 主仓库：管理面服务（manifest 目录/api key/钱包绑定/排行榜）+ 项目级制品链（intents/specs/plans/tests） | 01 / 06 |
| `coincall-gateway` | 数据面：402 付费调用网关（X-PAYMENT 验签/影子闸门/转发/settle_queue） | 02 |
| `coincall-bot-chain-api` | 链适配层：BOT Chain 全量链上操作（原 bot_chain_api，已迁移改名） | 05 |
| `coincall-contracts` | 合约仓：PayVault 及后续合约（Solidity + 测试 + 部署脚本） | 04 |
| `coincall-sdk`（P1） | 消费端 SDK + MCP skill | 03 |

> 原 `agent-pay-docs` 目录同步更名为 `coincall-docs`，即本文档族。
