# 04 — 结算层：PayVault 合约与 keeper（v2 终态）

> v2 变更：**v1 的平台账本方案整体作废**（充值托管/双录流水/三不变量/对账端点全部删除）。结算层 = 链上 PayVault 合约 + keeper 异步结算。资金永不过平台手（铁律 P7），提现路径永开（P8）。

## 1. 设计定位：手工复刻 EIP-3009 语义

Base x402 的支付承诺来自 USDC 合约原生的 `transferWithAuthorization`（EIP-712 签名授权，facilitator 可单方面上链执行）。BOT Chain 的 USDT 是无 3009 的 mock 币（实测 ABI 确认），因此我们自部署 **PayVault** 补出同语义：

| x402 原语（Base/USDC） | PayVault 复刻（BOT Chain/USDT） |
|---|---|
| USDC 内置 transferWithAuthorization | `chargeWithSig(consumer, provider, auth, v,r,s)` |
| EIP-712 授权签名 | 同构字段：from/to/value/validAfter/validBefore/nonce |
| facilitator 代提交 | keeper 调 `chargeWithSigBatch(auths[])` |
| facilitator 赞助 gas | keeper 账户付 gas（本链恒定 20gwei，极低） |
| 底层授权 approve | `USDT.approve(PayVault, 限额)`（消费者自主，随时可调） |

## 2. PayVault 合约规格（Solidity，零依赖单文件，目标 ≤160 行）

```solidity
// EIP-712 域与类型（与 EIP-3009 的 TransferWithAuthorization 结构对齐，铁律 P9）
struct Authorization {
    address from;        // 消费者钱包
    address to;          // 收款目标，强制 == 本合约地址（对齐 EIP-3009；防授权指向任意地址）
    uint256 value;       // 金额（最小单位）
    uint256 validAfter;
    uint256 validBefore;
    bytes32 nonce;       // 随机一次性（合约维护 usedNonces 映射防重放；bytes32=EIP-3009/x402 正典）
}
// domainSeparator = keccak256(EIP712Domain{name:"PayVault", version:"1",
//                                chainId:968, verifyingContract:<本合约地址>})

函数集：
    chargeWithSigBatch(
        (address provider, Authorization auth, uint8 v, bytes32 r, bytes32 s)[] calls
    ) external onlyOperator
        // 对每笔：验 deadline(now∈[validAfter,validBefore]) → 验 nonce 未用 →
        // 验 auth.to == 本合约 → ecrecover(digest(auth))==auth.from → 标记 nonce 已用 →
        // USDT.transferFrom(from, 本合约, value) → credits[provider] += value
        // （批量内一笔失败：跳过该笔不回滚整批，发 ChargeFailed 事件；
        //   单笔硬上限 MAX_BATCH=50；nonce 仅成功后烧毁（keeper 可携原签名重试坏账笔））
    providerWithdraw(address to, uint256 amount) external
        // provider（msg.sender）只提自己的 credits；to 缺省建议 = agentWallet（网关侧填）。
        // 无 pause、无 admin 锁、无时间锁 —— 铁律 P8
    operatorUpdate(address newOperator) external onlyOperator        // 单步（operator 与资金无关，I1 兜底）
    view: operator()、token()、DOMAIN_SEPARATOR()、credits(provider)、usedNonces(nonce)、totalCredits()、MAX_BATCH()

事件（记账键=provider 地址，网关侧 agentWallet 即 provider，免去 ERC-8004 tokenId 依赖）：
    Charged(address indexed provider, address indexed from, uint256 value, bytes32 indexed nonce)
    ChargeFailed(address indexed provider, address indexed from, string reason)   // reason 短码见 §3 keeper
    Withdrawn(address indexed provider, address to, uint256 amount)
    OperatorUpdated(address oldOp, address newOp)
```

**安全不变量（写进测试，铁律 P8 落地）**：
- I1：operator 无任何把合约内 USDT 转给"非 credits 对应 Provider"的路径（合约内唯一转出=providerWithdraw，按记账额度）；
- I2：同一 nonce 永不成功 charge 两次；
- I3：消费者资金主权 = USDT 层面的 approve/transfer 主权，合约无从干预（合约只做 transferFrom）；
- I4：合约内 USDT 余额 == Σ 未提现 credits（审计视图，链上即对账——**v1 的对账端点被链本身替代**）。

## 3. keeper（自建 facilitator 的 settle 侧）

```
输入：settle 队列（网关 02 写入的 {provider, auth, v,r,s, call_id} 成功调用记录）
流程：① 攒批（阈值配置化：笔数阈值 COINCALL_KEEPER_BATCH_SIZE，默认 3——演示场景故意取小值让 Charged 事件快速上屏；时间阈值 COINCALL_KEEPER_FLUSH_INTERVAL 秒，默认 30；上链单笔交易硬上限 50）
     ② 组装 chargeWithSigBatch calldata（经 coincall-bot-chain-api POST /contracts/send 提交，
        from=keeper 账户[coincall-bot-chain-api keystore 代管]，gas 固定价）
     ③ 解析回执：Charged → 结算流水落库；ChargeFailed(reason=insufficient_allowance)
        → 重试一次 → 仍失败：坏账表 + 通知网关拉黑该 api key（影子闸门 K 限幅兜底）
     ④ 队列持久化（DuckDB 表），宕机恢复后续批；授权 validBefore 过期的笔自动作废（=从未扣款）
gas：每批一笔链上交易（≈0.001 BOT 级），由 keeper 账户赞助
```

## 4. 与网关/消费面的接口（契约冻结）

- 网关 → keeper：`settle_queue` 表（call_id/provider_token_id/auth 六元组/created_at），只写不读；
- 网关 ← PayVault：余额类 eth_call（credits/allowance）经 coincall-bot-chain-api `POST /contracts/call`；
- keeper → 链上：仅经 coincall-bot-chain-api（铁律 P1）；
- Consumer/Provider 与合约交互（approve/withdraw）也全部经 coincall-bot-chain-api 既有端点（approve/transfer/contracts/send）——**05 无需为结算新增任何端点**。

## 5. 编译、部署与测试

- **编译**：solc 0.8.20+/shanghai（与链上已验证合约同口径，PUSH0 可用）；`py-solc-x` 离线编译，产物（bytecode+ABI JSON）入库；网络兜底链：GitHub 直下→镜像→`brew install solc`；
- **部署**：`coincall-bot-chain-api POST /contracts/deploy`（bytecode+abi+args=[USDT 地址, 初始 operator]），dry_run 预览→真实部署；部署地址记录进新服务配置并**回写 chains.py 体系**（地址单一来源纪律的跨仓约定：PayVault 地址由新服务配置持有，因其为本项目部署资产而非链预置）；
- **合约测试**（无 forge 环境，用 Python 全链路）：
  - unit（离线）：EIP-712 digest 构造与 eth_account 签名/验签一致性；calldata 组装正确性；
  - needs_funds（真实链）：部署→消费者 approve→单笔 chargeWithSig→credits 断言→重放同 nonce 失败(I2)→batch 混入坏账笔（跳过+事件）→providerWithdraw 到账→I4 余额审计断言；
- 合约未经审计：额度上限由消费者 approve 自主控制 + P8 提现永开 + 演示小额——写进演讲诚实边界。

## 6. 官方 AgentPay 上线后的迁移分级（协议形状已按 P9 对齐，迁移=换 scheme）

| 官方 eventual 形态 | 动作 | 成本 |
|---|---|---|
| 仍不可用/仅文档 | 零动作（演示话术含"官方仍占位"） | 0 |
| 官方 facilitator/支付合约上线 | payment_scheme 增加 "eip3009-native" 适配器：settle 改提交官方合约，网关/SDK 骨架不动 | ~半天 |
| 官方引入带 3009 代币 | SDK 加对应 scheme 的 EIP-712 签名器（eth_account sign_typed_data 现成），双 scheme 灰度 | ~1 天 |
| Base 适配（V1.5/V2，可选） | 同一 ChainAdapter 接口插入 Base Sepolia/主网 adapter；Base 上**零自建合约**（USDC 原生 3009，keeper 直接提交授权） | 见 08 任务卡 |

## 7. 明确删除的 v1 内容（防止回潮）

~~平台代管收款账户~~ / ~~充值确认轮询~~ / ~~ledger 双录账本与三不变量~~ / ~~对账端点~~ / ~~PaymentVault deposit 托管模式~~ ——被"链上记账即对账 + 消费者钱包直扣"结构性替代。失败退款逻辑同样不存在（后付费时序：失败=从未扣款）。
