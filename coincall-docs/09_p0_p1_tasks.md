# 09 — P0~P1 逐项详细任务书（展开版，不浓缩）

> 本文是 00 §6 优先级的完全展开。每项按固定结构写：**这是什么（人话）→ 现状 → 具体要建的东西（逐条）→ 怎么做（步骤）→ 怎么算完成（验收）→ 依赖谁 → 预估**。审阅本文档即可对全部执行细节提出异议。

---

# P0-1：coincall-bot-chain-api 的三个身份端点

**这是什么**：让"Provider 在 BOT Chain 上完成身份注册、绑定收款钱包、写元数据"这三件事可以只靠调 API 完成。这是全套付费体系里**唯一的链上端点开发**，其余所有链上动作都被 coincall-bot-chain-api 既有端点覆盖。

**现状**：coincall-bot-chain-api 的 M6 模块已有 6 个端点（注册身份、身份聚合视图、信誉、验证、注册表分页、合约状态），但注册身份后拿不到"我是几号 Agent"（要人工解析链上事件）、想绑收款钱包没有入口（链上方法 `setAgentWallet` 存在但未暴露）、元数据读写同样未暴露。

**具体要建**（三个端点，全部挂在 M6 模块）：

1. `POST /api/v1/agent-identity/{token_id}/wallet` —— 绑定/更新收款钱包
   - 请求体：`{owner, wallet_address, deadline?, signature?, dry_run:true}`（dry_run 默认只返回交易预览，这是 coincall-bot-chain-api 的全局铁律 A4）
   - 链上方法：`setAgentWallet(uint256 agentId, address newWallet, uint256 deadline, bytes signature)`（ABI 已在探测存档 results/d3_probes/abi_IdentityRegistry.json 里，精录即可）
   - 签名参数处理：若 `wallet_address` 是 coincall-bot-chain-api 的代管账户，服务可代签；否则要求显式传入 signature。**签名怎么构造是本项目唯一未实测的链上悬念**——答案在本地 264KB 的 IdentityRegistry 已验证源码存档里（第一步读它定案，不盲测）
   - 校验：owner 必须是该 tokenId 的 owner（eth_call ownerOf）
2. `GET /api/v1/agent-identity/register-result/{tx_hash}` —— 注册结果解析
   - 输入注册交易的哈希，查回执、解析 Transfer 铸造事件，直接返回 `{status, agent_ids[], owner}`；未上链返回 `{found:false}`
   - 解析逻辑在已有测试里验证过（test_06 的注册用例就手工做了这件事），只是包成端点
3. `PUT/GET /api/v1/agent-identity/{token_id}/metadata` —— 元数据读写（顺带项，不想做可砍）
   - 链上方法 `setMetadata(uint256,string,bytes)`/`getMetadata`；value 限 UTF-8 JSON ≤4KB
   - 用途：服务清单 hash 锚定上链（P2 用，但端点便宜先备好）

**怎么做**：① 读源码存档定签名格式（半天内消除悬念，结论回写 05 文档）→ ② 从探测 ABI 精录两条方法进 core/abis → ③ 测试先行写用例（红态提交）→ ④ 实现三端点（复用 M6 现有的 TxService/contract_at/事件解码模式）→ ⑤ needs_funds 全链路回环真实上链 → ⑥ 质量门（ruff/mypy/pytest 覆盖率≥80%）→ ⑦ 重启 8010 服务 + curl 冒烟 → tag `d7-api-ext`。

**验收**：三端点 unit 全绿；needs_funds 回环通过（注册新身份→解析出 agentId→绑定新钱包→聚合视图读到新地址→元数据写读一致）；预计消耗 <0.05 BOT。

**依赖**：无前置。**被依赖**：P1-2（Provider 接入流程用端点 1、2）。
**预估**：0.5~1 天。

---

# P0-2：PayVault 支付合约（结算的链上心脏）

**这是什么**：一个部署在 BOT Chain 测试网上的 Solidity 合约（≤160 行，零依赖单文件，口径与 04 §2 一致），功能一句话——**持有消费者签名授权的批量扣款权与 Provider 的应收账本**。它手工复刻了 Base USDC 原生 EIP-3009 的语义（那条链的 USDT 没有），是"x402 模拟"的全部链上实现。完整规格见 04 文档。

**现状**：从零新建。工具链注意：coincall-bot-chain-api 仓库至今没有 solidity，需要一次性引入 solc 编译环境（py-solc-x，下载失败有镜像/brew 兜底）。

**具体要建**：

1. 合约本体（04 §2 规格已冻结）：
   - `chargeWithSigBatch(calls[])`：keeper 唯一可调；逐笔验 deadline→nonce 未用→to==本合约→ecrecover 验签→transferFrom 消费者钱包进合约→按 provider 地址记账（agentWallet 即 provider）；单笔失败跳过并发 ChargeFailed 事件（不毒死整批）
   - `providerWithdraw(tokenId, to, amount)`：按记账额度提现，**无 pause 无 admin 锁**（铁律 P8：任何 bug 最坏=服务停摆，钱永远能提走）
   - 事件：Charged / ChargeFailed / Withdrawn
   - 四条安全不变量（I1 operator 无提钱路径 / I2 nonce 不双扣 / I4 合约余额==Σ未提现记账）写进测试
2. EIP-712 域定义与 Python 侧配套：digest 构造函数（与合约逐字节一致）——放新服务 SDK 侧，golden vector 测试锁死
3. 编译与部署脚本：solc 0.8.20/shanghai 离线编译产物入库；部署走 coincall-bot-chain-api 的 `POST /contracts/deploy`（dry_run 预览→真实部署）；operator 初始地址=keeper 的 coincall-bot-chain-api 代管账户

**怎么做**：① 定 solidity 环境（solc 安装冒烟）→ ② 写合约（测试先行无 forge，直接配 Python 全链路测试）→ ③ 编译产物入库 → ④ 部署到 968 → ⑤ 全链路 needs_funds 测试。

**验收**：部署成功；消费者 approve→单笔 charge→记账断言；同 nonce 重放失败；批内混坏账笔跳过+事件；withdraw 到账；合约内余额==Σ记账（链上即对账）。未经审计如实写进演讲诚实边界。

**依赖**：无前置（可与 P0-1 并行）。**被依赖**：P0-4（网关验签要知道合约地址与域分隔符）、P0-5（keeper 调它）、P1-1（SDK 签名用同域）。
**预估**：1 天（含工具链引入与全链路测试）。

---

# P0-3：新服务地基与接口契约冻结

**这是什么**：新建付费网关服务本体（正式项目名待定，即"42/BOOTH/售货机"那个）的工程骨架，外加**把四个"多模块共享的数据结构"先写成代码文件冻结**——这一步是之后三条线并行开发互不阻塞的前提，投入半天换来并行权。

**现状**：全新目录/仓库（与 coincall-bot-chain-api 通过 HTTP 对接，绝不共享代码）。

**具体要建**：

1. 工程骨架：pyproject（**逐字复制 coincall-bot-chain-api 的 ruff/mypy/pytest 钉死配置**）+ FastAPI + lifespan + DuckDB 初始化 + CONSTRAINTS.md（继承 00 铁律 P1~P9 + 静态检查门 + 犯错沉淀区）+ pre-commit 钩子（同款）+ git 仓库与 tag 纪律
2. 建全部数据库表：calls / settle_queue / services / providers / api_keys / receipts / bad_debt（DDL 从 02/03/04/06 汇总）
3. **四份契约文件**（冻结，其他任务只 import 不修改）：
   - `contracts/calls.py`：calls + settle_queue 表模型
   - `contracts/manifest.py`：ServiceManifest pydantic 模型（含 chain 字段）
   - `contracts/payment.py`：X-PAYMENT 头格式、Authorization 六元组、EIP-712 digest 构造（与 P0-2 合约 golden vector 对齐）、402 质询模型（字段名对齐 x402：scheme/max/exact/asset/chain/payTo/resource）
   - `contracts/schemes.py`：**payment_scheme 与 ChainAdapter 两个 Protocol 接口**（以 Base USDC x402 形态为设计标尺，见 00 §4；V1 只挂 BOT Chain 实现）
4. 配置项：BOT_CHAIN_API_BASE、PAY_VAULT_ADDRESS、KEEPER_ACCOUNT 等环境变量

**怎么做**：一次性搭好，空跑 pytest 绿 + 静态三连绿即完成。tag `p0-base`。

**验收**：`pytest -m unit` 空跑绿；ruff/mypy 零 error；四契约文件 import 无环。
**依赖**：P0-2 的域定义（契约文件里的 digest 函数要和合约对齐，golden vector 一起锁）。**被依赖**：之后一切。
**预估**：0.5 天。

---

# P0-4：402 付费网关（运行时心脏）

**这是什么**：付费调用的统一入口 `POST /call/{service_id}`，把这句话变成代码："收到带支付签名的请求→本地验签→影子闸门→放行转发给 Provider→结果透传；Provider 失败则从未扣款"。消费者看到的就是 402→付款→数据这个闭环。

**现状**：从零建在 P0-3 骨架上；开发期不需要真实 Provider（manifest 用 fixture 注入，Provider 端点用本地 mock http 服务）。

**具体要建**（按请求流顺序）：

1. **api key 认证**：查 api_keys 表（03 签发），吊销即拒
2. **X-PAYMENT 解析与验签**（payment_scheme 的 verify 实现）：base64 解码→字段完整性→ecrecover==该 key 绑定的消费者钱包→deadline 窗口→value==服务定价→nonce 未重放（Redis）
3. **链上约束检查**：eth_call（经 coincall-bot-chain-api /contracts/call）读 USDT allowance(consumer→PayVault) 与 balance，短缓存 30s
4. **影子闸门**：Redis 原子操作维护每 key 在途金额与笔数（K=3 可配），条件不满足直接 402——性能层非资金层，fail-closed
5. **402 质询响应**（格式见 02 §3，x402 同构）：含金额/收款说明/approve 指引/scheme
6. **schema 校验与幂等**：请求体按服务 input_schema 校验（不合规不计费）；X-Idempotency-Key 重放返回上次结果
7. **代理转发**：httpx 转发 manifest.endpoint（http_json 外部 URL / internal 本地 handler 两种），超时按 manifest
8. **收尾**：成功→calls 流水+settle 队列+收据（HMAC 签名，X-Receipt 头）；失败→流水 aborted，零扣款
9. **错误模型**：沿用三段错误风格，401/402/404/409/422/502 语义见 02 §2

**怎么做**：契约驱动——02 文档的时序逐条对应测试用例；mock Provider 覆盖 success/5xx/超时三路；验签各失败分支独立用例。

**验收**：02 §7 三条（质询格式逐字段断言、幂等不双计、验签各分支独立可测、影子闸门并发不超限、Provider 失败不产生 settle 记录）；同 key 50 并发无超限。
**依赖**：P0-3 契约；联调期接 P0-2 合约地址。**被依赖**：P1-1（SDK 对接它）、P1-3（读它的流水）。
**预估**：1.5 天。

---

# P0-5：keeper 结算器（自建 facilitator 的 settle 侧）

**这是什么**：一个后台进程（新服务内的常驻任务），把网关攒下的成功调用签名授权**批量上链结算**：组批阈值配置化：笔数默认 **3 笔**（COINCALL_KEEPER_BATCH_SIZE，演示取小值便于快速上屏）、时间默认 30 秒（COINCALL_KEEPER_FLUSH_INTERVAL），发一笔 `PayVault.chargeWithSigBatch` 交易。它扮演 x402 架构里 facilitator 的结算半边（验证半边在网关里）。

**现状**：从零建；链上提交全部经 coincall-bot-chain-api（`POST /contracts/send`，from=keeper 代管账户）。

**具体要建**：

1. 队列消费循环：读 settle_queue pending → 组批 → calldata 组装 → coincall-bot-chain-api 提交 → 等回执
2. 回执处理：Charged 事件→流水置 settled；ChargeFailed(余额不足)→重试一次→仍失败置 bad_debt + **通知网关拉黑该 api key**（影子闸门 K 笔兜底之外的最后防线）
3. 过期作废：授权 validBefore 已过的笔直接置 expired（等于从未扣款，无人受损）
4. keeper 账户管理：coincall-bot-chain-api keystore 代管账户，README 注明需保持少量 BOT 付 gas（每批 ≈0.001 BOT）

**怎么做**：先写用例（正常批/含坏账批/过期批/宕机恢复续批四路）再实现；与 P0-4 在 settle_queue 表契约处联调。

**验收**：真实链上批量结算成功且合约记账与流水一致；坏账笔跳过+事件+拉黑联动；kill keeper 后重启续批不丢单。
**依赖**：P0-2 合约（部署地址）、P0-3 队列表。**被依赖**：P1-3（结算流水入排行榜）。
**预估**：0.5~1 天。

---

# P1-1：Consumer SDK 与本地付费钱包

**这是什么**：消费者侧的全部接入物——一个 pip 包（`<项目名>-sdk`）+ MCP skill。赛时"5 分钟接入"的承诺物：任何队把自己的 Agent 接进市场 = 装包+三个环境变量+一个函数调用。

**具体要建**：

1. **本地付费钱包**：SDK 初始化时 `Account.create()` 生成专用 EOA（或导入已有），私钥存消费者本地（keyring/文件/env），平台拿不到；提供 `PUT /consumer/me/bind-wallet` 绑定到 api key
2. **资金准备引导**：`approve_vault("5")`（经 coincall-bot-chain-api approve 端点或本地签）；`balance()` 返回钱包 USDT/授权额度/可用额度（含在途）
3. **签名支付**：`call(service_id, params)` 内部组装 Authorization 六元组→本地 EIP-712 签名→X-PAYMENT 头→发网关；402 捕获并抛带指引的异常；收据验签；本地预算计数器（超预算拒调）
4. **MCP skill**：stdio MCP server，暴露 `catalog` 与 `paid_service_call` 两个工具（Agent 框架挂载即接入）；《5 分钟接入》README（三个环境变量）
5. 消费面服务端小件：api key 签发/吊销/列表端点（03 §2）

**验收**：03 §7 三条（key 生命周期、钱包生成→转入→approve 全链路、SDK 三路测试 success/402/aborted）；MCP server 可被标准 client 调通。
**依赖**：P0-4 网关（对接）、P0-3 契约（digest/头格式）。**预估**：1 天。

---

# P1-2：Provider 接入层（形态对齐官方 Agent OS）

**这是什么**：Provider 挂服务的全部服务端支撑——manifest 契约、服务目录、注册流程端点。**注意范围**：真实第三方 Provider 的选品与预集成归你规划侧，这里只保证"接入层抽象"就位，你的 Provider 按契约挂上即售。

**具体要建**：

1. providers/services 表与 API（01 §4/§5）：登记 provider（校验 8004 身份在链上存在）、发布/暂停服务、机读目录 `GET /services`（带 ETag，Agent 发现服务的入口）
2. manifest 校验器：定价精度（amount_raw 权威）、endpoint 双形态（`http_json` 外部转发 / `internal` 平台内置 demo 兜底）、input/output schema 完整性、**chain 字段**（V1 恒 968，接口为多链预留）
3. Provider 接入五步流程端点串联（01 §2）：注册身份（P0-1 端点 2 拿 agentId）→绑钱包（P0-1 端点 1）→登记→发布→（可选）metadata 锚定
4. **官方形态对齐**（05 §6.5）：响应字段命名向官方 identity-api 靠拢（如 by-erc8004 查询语义、wallet-binding readiness）——官方服务开放后调用方可换 base_url 迁移

**验收**：01 §7 三条（manifest 校验全分支、五步接入 needs_funds 真实走通、非法 manifest 拒绝）；机读目录可被 SDK catalog 消费。
**依赖**：P0-1（两个端点）、P0-3 契约。**预估**：1 天。

---

# P1-3：数据与排行榜

**这是什么**：把运营事实（calls/settle 流水）变成两类产出——排行榜/overview API（demo 大屏与演讲开场数字）和链上 proof（"每个数字都可在 scan.bohr.life 核验"）。

**具体要建**：06 文档的三个 DuckDB 视图（服务日汇总/服务总分/Provider 信用画像）、四个只读 API（`/leaderboard/services|providers`、`/store`、`/stats/overview`）、`/leaderboard/providers/{id}/proof`（settlements 链上交易哈希清单）。**排序口径固定收入优先**（收入=最硬信誉，无人工评价）。v2 增量：settle/bad_debt 流水纳入视图（status 枚举新增 settled/bad_debt）。

**验收**：06 §5（fixture 数据逐字段断言、空库优雅返回、overview GMV 与链上 Charged 事件总额一致）。
**依赖**：P0-3（表）、P0-4/5（流水产生方）；开发期用 fixture 可与 P0 并行。**预估**：0.5 天。

---

# P1-4：演示集成（比赛交付的收口）

**这是什么**：三幕演示剧本的工程化——内置 demo 服务、彩排、录档。剧本见 07 文档（第三幕画面更新为：keeper 批量结算的 Charged 事件实时刷屏 + Provider withdraw 到账）。

**具体要建**：

1. 三个内置 demo 服务（07 §2）：`svc_chain_report`（我们，internal 型）、`svc_translate`（友队 http_json 或我方兜底）、`svc_contract_scan`（internal 型）；internal handler 规则（endpoint `internal://` 前缀网关本地执行，避免 demo 网络单点）
2. 第四幕可选段（若做 V1.5 Base Sepolia 最小闭环，见 08 任务卡 W9，1 天可选）
3. 演示彩排走一遍 07 剧本并录档 results/demo.md；README（快速启动/接入/诚实边界）

**验收**：剧本全程无人工干预跑通；友队掉线兜底切换演练通过。
**依赖**：P0/P1 全部就绪。**预估**：1 天。

---

# 汇总视图

| 任务 | 仓库 | 预估 | 前置 |
|---|---|---|---|
| P0-1 身份端点 | coincall-bot-chain-api | 0.5~1d | 无 |
| P0-2 PayVault 合约 | 新仓库（contracts/） | 1d | 无（与 P0-1 并行） |
| P0-3 地基+契约 | 新仓库 | 0.5d | P0-2 域定义 |
| P0-4 402 网关 | 新仓库 | 1.5d | P0-3 |
| P0-5 keeper | 新仓库 | 0.5~1d | P0-2/3（与 P0-4 并行） |
| P1-1 SDK+本地钱包 | 新仓库 | 1d | P0-3/4 |
| P1-2 Provider 接入层 | 新仓库 | 1d | P0-1/3 |
| P1-3 数据排行 | 新仓库 | 0.5d | P0-3（fixture 并行） |
| P1-4 演示集成 | 新仓库 | 1d | 全部 |

关键路径 ≈ 5 天满负荷；赛时 40h 砍 P2 全部+P1-4 的第四幕，保 P0 五项+P1 最小三件（SDK/接入层/排行）。
