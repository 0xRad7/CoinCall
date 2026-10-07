# 07 — 赛时演示剧本与服务选品

> 前置阅读：全部。演示时长口径：6 分钟参赛演示 + 40h 现场把玩。

## 1. 三幕剧本（每幕 ≤90s，一句主线话术）

**开场数字**（`GET /stats/overview` 大屏）：平台总调用 / 总 GMV / 活跃 Provider 数。

### 第一幕：挂服务（供给侧 30 秒）
话术："任何 Agent 能力，30 秒变成收费服务。"
1. 友队/评委侧注册 8004 身份（`register` → `register-result` 拿 agentId，05-C 的端点让这步流畅）；
2. 绑定 agentWallet（05-A）；
3. `POST /services` 发布 manifest（定价 0.01 USDT/次）→ 机读目录 `GET /services` 立即可见。

### 第二幕：付费调用（核心闭环 90 秒）
话术："Agent 只需要会 HTTP。"
1. 我们的 Consumer Agent（真实 LLM 工具循环）执行演示任务，其中一步需要翻译/数据能力；
2. 大屏显示它从目录选中 `svc_translate_v1` → `POST /call/…` → **402 质询 JSON 投屏**；
3. SDK 自动补足（余额足）→ 转发 → 译文返回 → 收据头特写（`X-Charged-Raw: 10000`）；
4. 反向镜头：Provider 队的 agentWallet `GET /accounts/{addr}/balances` 收入 +0.01 USDT（链上真钱）。

### 第三幕：结算与信誉（60 秒）
话术："收入就是最硬的信誉，每一笔都在链上。"
1. keeper 结算画面：付费调用攒满一小批（默认 3 笔，阈值配置化）后，PayVault 的 Charged 事件在大屏实时刷出（批量结算的可视化）；
2. Provider 提现：`providerWithdraw` → USDT 到 agentWallet（经 coincall-bot-chain-api）；
3. 排行榜：`GET /leaderboard/providers`（收入优先）+ proof：settlements 交易哈希 → scan.bohr.life 现场验证。

### 第四幕（可选，V1.5）：同构性证明（30 秒）
话术："协议层与官方 x402 同构——切一条链照样跑。"
Base Sepolia 上用带原生 3009 的 USDC 跑同一 SDK 的付费调用（keeper 直接提交 transferWithAuthorization，零自建合约）。

## 2. Demo 服务选品（≥3 个，至少 1 个非我方运营）

| 服务 | 单价 | 运营方 | 端点形态 | 存在意义 |
|---|---|---|---|---|
| `svc_chain_report` 链上数据报告 | 0.05 | **我们**（包装 coincall-bot-chain-api 数据端点） | 内置（网关本地 handler） | "第一个付费端点=网关自己"，展示平台自举 |
| `svc_translate` 技术翻译 | 0.01 | 友队（赛前预集成）或我们兜底 | Provider 自运营 http_json | 展示第三方接入 + 真实跨队资金流 |
| `svc_contract_scan` 合约快查 | 0.02 | 我们（另子项目能力或 LLM 包装） | 内置 | 类目多样性 |

- 内置 handler 规则：manifest `endpoint.type=http_json` 且 `endpoint.url` 为 `internal://` 前缀时网关本地执行（不走外网代理）——避免 demo 网络单点；
- 兜底策略：若友队掉线，`svc_translate` 切我方备用端点，manifest PATCH 即切换（01 §5）。

## 3. 现场把玩路径（40h 开放体验）

1. 扫码页 → 一键签发 api key；
2. 本地钱包页一键生成（SDK `Account.create`，私钥不出观众机器）→ 领水 → 页面引导 `approve(PayVault)`（页面按钮调 coincall-bot-chain-api `/tokens/erc20/approve`）；
3. 在 `/store` 挑服务 → 页面内置 playground 发起付费调用（复用 coincall-bot-chain-api 的 Swagger 交互理念）；
4. Provider 侧：`5 分钟接入.md` + MCP skill 两个工具（`catalog` / `paid_service_call`）。

## 4. 演示风险与兜底

| 风险 | 兜底 |
|---|---|
| 友队 Provider 掉线 | 02 为 postpay 时序：失败=从未扣款（无退款流程）；manifest 热切换备用端点 |
| 钱包 approve/领水确认慢（>60s） | 赛前预备一只已完成领水+approve 的备用观众钱包，一键顶替；链上确认轮询走 coincall-bot-chain-api |
| setAgentWallet 签名要求（05 不确定点） | W0 首日实测；不可用则 Provider 绑定改由后台预置（赛前批量完成，演示只展示已绑定状态） |
| 链 RPC 抖动 | coincall-bot-chain-api 已有重试/POA 适配；剧本里链上步骤均有回执哈希可人工补验 |

## 5. 诚实边界（写进演讲结尾）

链上实测活跃度有限（我们尽调数据），本平台当前价值 = 赛时真实闭环 + Agent 经济的协议位（x402 时代）+ 全链上可验证的结算。不做 GMV 修饰，overview 的数字就是全部真实发生过的调用。

## 附录：被否决方向的死结备忘（防止回潮）

任务赏金市场（escrow+验收）三重死结：
1. 能写出验证器的雇主不需要市场（自己有 coding agent）；
2. 非确定性验证（LLM-judge/多 Agent 交叉/人工）= 纠纷机器；
3. 微额抽佣覆盖不了争议运营成本（Bounties Network/Dework 前车之鉴）。
同步交付（HTTP 请求-响应，当场验货+复购纪律）使验证问题被结构性取消——本项目全部设计均以此为前提。
