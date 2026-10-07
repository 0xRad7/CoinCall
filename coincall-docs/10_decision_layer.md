# 10. 决策层（L1：履约记录公开 × 付费反馈权 × 带证明的排序）

> 状态：开发中（2026-10-06 定稿并发实施）。L2（ZK 匿名反馈）后续可选，本文仅留接口。
> 赛题映射：帮助 Agent 选择可靠服务（决策 API 带证明物）× 一次使用的结果成为公共参考信息（履约聚合+付费反馈，摘要锚链）。

## 0.5 本轮裁决（2026-10-06 发起人）

- **抗刷分推迟**（L1/L2 链上启发式与集中度曝光全部后置）；
- **新增指标**：`revenue_distinct_payers`（窗口内去重支付钱包数，与收入总量并列进证据，评分各占 0.5 权重）；**时间新鲜度** `freshness`（半衰期 48h 的指数衰减，last_activity 起算）成为第四信号，权重 {rev .4 / ful .25 / fb .2 / fresh .15}；
- **演示时间注入**：决策 API 支持 `as_of` 参数（默认 now）——演示时可 rewind/freeze 新鲜度打分，展示"同类 Provider 随时间衰减重排"；
- **类目分区**：manifest 增 `category`（受控词表 translation / data-feed / on-chain-query / analysis / agent-tool / other，默认 other）+ `tags[]≤5`；`GET /decision/categories` 出词表计数；排序默认在分区内。

## 0. 总纲

**决策层 = 收入（链上真相）× 履约（签名聚合）× 反馈（付费修正），三者全部带证明物。**
Agent 拿到的不是黑箱分数，是"证据包"——每个分量可独立核验。ZKCP 出局（prepay 语义与 postpay 架构冲突，谓词电路不现实，见对话纪要）；L1 不用 ZK，反馈权以"收据绑定+一次性核销"的朴素形态实现，nullifier 表即 L2 匿名化的地基。

## 1. 履约记录公开（fulfillment telemetry）

**来源**（全部已有，仅补一处）：网关 calls/settle_queue（成功/中止/坏账/在途）+ keeper 结算事实 + Charged 链上事件。**补**：calls 落库增加 `latency_ms`（路由层已计时未持久化——W3 实现时被 `del started` 略过）。

**指标**（按 service 聚合，窗口化 24h / 7d / all）：
- `success_rate = settled+success / (settled+success+aborted)`（坏账单列不入分母——它是消费者侧违约不是服务侧失败）
- `p50/p95 latency_ms`、`bad_debt_count`、`total_calls`、`last_call_at`

**锚链**：每聚合窗口（或每 N 笔结算）取聚合 JSON 的 sha256 → 经 bot-chain-api `setMetadata`（ERC-8004）写到 **Provider 身份 token** 的 `coincall:fulfillment:v1` 键（value=摘要+指针）。锚定动作者=keeper 循环里的新 anchor 任务（复用 keeper 的链上提交通道与出资账户）。**链上只放聚合摘要**（gas 便宜、防篡改锚点），原始数据在 core API 按摘要可核。

**端点**：`GET /fulfillment/services/{service_id}?window=` → 指标 + digest + 锚定交易哈希。

## 2. 付费反馈权（朴素版：收据绑定 + 一次性核销）

**前置升级——收据改 Ed25519 自验签**：现为 HMAC（密钥在网关，别人无法验证，与"公共信息"叙事矛盾）。改为网关 Ed25519 密钥对签名收据（公钥经 core 发布），任何持有公钥者（Agent/第三方/链下审计）可离线验证收据真伪。**迁移兼容**：双签名过渡期一个窗口，旧 HMAC 收据只在本机验证（诚实边界注明）。

**反馈流**：
1. 调用成功后消费者持收据提交 `POST /feedback {service_id, receipt_id, receipt_sig, rating(1-5), comment?}`；
2. core 验签（Ed25519 公钥）→ 核对收据指向该服务、状态 success、**未核销过**（`feedback_nullifiers` 表，receipt_id 主键）；**防自评**：收据付款人 ≠ 该服务 provider.wallet；
3. 通过 → 落库（含"已验证付费反馈"标记）→ nullifier 烧掉（一收据一反馈）；
4. **链上只落聚合**：每窗口把 `feedback_agg {count, avg, digest}` 锚到同一 setMetadata 键族（`coincall:feedback:v1`）——逐条上链是 gas 浪费+垃圾面；原始反馈走 core API 带 receipt 证明可查。

**抗刷分账本**（朴素版即成立）：刷一票=真实付费一次，成本=服务单价；同一钱包无限发 key 无意义（资格在收据不在 key）；L2 升级=同表改 ZK 成员证明（nullifier 语义已就位）。

## 3. 决策 API（带证明的排序）

`GET /decision/services?category=&window=` → 排序 + 每行证据包：

```
{ service_id, score, weights: {revenue: .5, fulfillment: .3, feedback: .2},
  components: {
    revenue:     {value_raw, charged_count, proof: "leaderboard proof 端点引用"},
    fulfillment: {success_rate, p95_ms, window, proof: {digest, anchor_tx}},
    feedback:    {count, avg, window, proof: {digest, anchor_tx}, verified_paid: true},
  } }
```

- **评分公式透明可辩**：score = w₁·norm(revenue) + w₂·success_rate·latency_bonus + w₃·bayesian_avg(rating)（低样本向全局先验收缩，防 3 票满分压过 300 票 4.8）。权重与公式**写进响应**，Agent 可自行重算；
- `GET /decision/explain/{service_id}`：单服务完整证据包（含履约明细与反馈原始条目）；
- 排序默认窗口 7d，`window=all` 给长期信誉。

## 4. 落点与改动面

| 仓 | 改动 |
|---|---|
| gateway | calls 表补 latency_ms；收据 Ed25519 化（公钥导出给 core）；keeper 循环加 anchor 任务（履约+反馈摘要锚链） |
| core | fulfillment 聚合与端点；feedback 提交/验签/nullifier/查询；decision API；发布网关收据公钥 |
| bot-chain-api | **零新端点**（setMetadata/reputation 均已有，实施时核对 giveFeedback 实际签名语义） |
| console | 目录卡履约徽章（成功率/延迟/反馈星标+已验证标记）+ 服务详情证据包页 |
| contracts | 零改动 |

## 5. 测试与验收要点
- 履约指标与 calls 流水逐字段对账（fixture+真链）；摘要与锚定交易可核（scan 哈希回读一致）；
- 反馈五路：正常/重复收据 409/跨服务收据 422/自评 403/验签失败 401；Ed25519 收据第三方可离线验证（测试里用公钥裸验）；
- decision 权重响应=文档声明；低样本收缩用例（3 票新服务不压 300 票老服务）；
- 端到端：一次真实付费调用 → 履约窗口聚合 → 摘要上链 → 反馈提交 → 决策排序变化，全程证明物可点验。

## 6. 顺序与估时（供排期）
① 收据 Ed25519 化+latency 落库（0.5d）→ ② 履约聚合+锚链任务+端点（0.5~1d）→ ③ 反馈权全链（0.5~1d）→ ④ 决策 API（0.5d）→ ⑤ 控制台展示（0.5d，可与 ④ 并行）。合计 3 天内。

## 7. 诚实边界
- 履约数据由网关签名，平台理论上可伪造——摘要锚链把伪造成本做成"必须持续伪造且链上留痕"；根治需多签网关（超出范围，注明）；
- 反馈是主观修正项（w₃ 最小），不参与任何裁决；
- Ed25519 迁移期旧收据降级本机验证；ReputationRegistry 若逐条 giveFeedback 语义更强，实施时再评估是否双写（默认只走聚合锚）。
- L2 接口预留：feedback_nullifiers 表即匿名成员证明的核销地基，升级时只加电路与树根锚定，不动业务表。
