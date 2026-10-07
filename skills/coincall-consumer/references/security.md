# CoinCall 消费安全五问五答（劝退项自查清单）

> 改写自 coincall-docs/design/agent-wallet-trust.md §3「劝退项自查清单」。
> 用途：用户问起安全边界时的人话回答；Agent 接入前的自查清单。
> 每答都给"怎么亲眼验证"的命令——安全主张必须可核查，不要求信任任何人。

## 问 1：Agent 最多能花掉我多少钱？

**答**：三重硬顶封死，且**落盘持久**（重启不清零）：

| 顶 | 环境变量 | 语义 |
|---|---|---|
| 总额 | `COINCALL_TOTAL_BUDGET_RAW` | 进程生命周期内累计上限 |
| 日额 | `COINCALL_DAILY_BUDGET_RAW` | UTC 日界自动重置 |
| 单笔 | `COINCALL_PER_CALL_BUDGET_RAW` | 每笔调用帽（目录价之外的冗余层） |

任一触顶 → L0 在**签名前**拒绝（连请求都不发出），自动进入只读（目录可看、付费拒绝）。
最坏损失还有一个物理上限：钱包里只有你放进去的小额。

验证：`python scripts/call.py status` 看 `policy` 块；`python scripts/call.py report` 看余量。

## 问 2：它会不会把钱转给陌生地址？

**答**：不会——支付授权的收款方被锁死在 PayVault 合约（EIP-712 的 `to` 字段由 SDK 组装，
Agent 的输入里没有这个字段；合约再验 `to==本合约`）。approve 也只能指向 PayVault。
MCP 工具面**没有转账、没有 approve 工具**——工具面上根本不存在"把钱转去某地址"这个动词。
服务白名单（`COINCALL_ALLOWED_SERVICES`，默认拒绝）再限一层能向哪些服务付费。

验证：`python scripts/call.py quote <服务ID>`，看 `payee` 恒为 PayVault 地址。

## 问 3：金额会不会被 prompt 注入改大？

**答**：改不了——Agent 的任何输入里都**没有金额字段**。金额只来自平台目录定价，
并在三处强校验：SDK 组装授权时取 `catalog.pricing.amount_raw` → 网关核验"金额==定价"
→ PayVault 合约验签。注入能做的最坏只是"反复调用某个贵的服务"，这退化成问 1，
被三重预算+速率限制（`COINCALL_MIN_INTERVAL_S` / `COINCALL_MAX_CALLS_PER_HOUR`）挡住。

验证：`python scripts/call.py quote <服务ID>` 的 `price_raw` 与目录一致；账单里每笔
`charged_raw` 都等于该服务定价。

## 问 4：钱花哪了我能看到吗？

**答**：三方对账，全部可查：

1. **本地账本** `~/.coincall/ledger.jsonl`（只追加，三段：intent→receipt→onchain）；
   `python scripts/call.py report` 一屏看完（已花/剩余/最近每笔收据号与金额）。
2. **平台流水**（core 侧该 api key 的 calls 记录）。
3. **链上事件**（PayVault 的 Charged 事件，测试网 968 rpc.bohr.life 可查）。

每笔成功调用都有收据号（receipt_id）与网关签名头（X-Receipt-Sig）。

## 问 5：出问题我能立刻停吗？

**答**：三级刹车，从秒级到即时：

1. **秒级**：预算本来就是硬顶（触底自动只读）；删除/改小预算 env 重启即生效。
2. **即时止血（链上主权）**：撤回 USDT.approve 或转走钱包余额——平台无法从零授权/
   空钱包扣到任何东西，这一级不依赖任何 CoinCall 代码。
3. **断通道**：吊销 api key（core 侧失效）。

另外：**失败的调用从未扣款**（后付费时序——网关确认服务成功才结算），不存在
"重试风暴偷偷扣钱"的路径；幂等键保证同参数重复请求不会双扣。

## 附：密钥被偷怎么办（诚实回答）

专用钱包的最坏损失 = 你放进去的钱。请像对待现金零钱包一样对待它——这就是
本方案不接主钱包、不做托管的理由。应对：撤 approve / 转空余额 / 吊销 key / 换钱包。
