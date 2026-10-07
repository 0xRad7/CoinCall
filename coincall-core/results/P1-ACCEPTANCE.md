# P1 验收对账（链 20261005221052）

> 对账基准：`coincall-docs/09_p0_p1_tasks.md` P1-1~P1-4 各节"验收"行。
> 结论：**四项全过**。日期：2026-10-05（本机）。自动验收授权沿用。

| # | 验收行（要点） | 结果 | 证据 |
|---|---|---|---|
| P1-1 | SDK：key 生命周期可用、钱包生成→转入→approve 全链路、三路测试、MCP 可被标准 client 调通 | ✅ | coincall-sdk tag `d0`：41 unit/90.58%；T16~T19 全绿（T18 三路 MockTransport 零网络；T19 真实子进程 stdio）；**黄金向量=第三份独立实现，digest/structHash/domainSeparator 逐字节一致且 RFC6979 确定性签名逐字段相同**；live：付费调用 rcp_0fe53e8e424e、余额 Δ=-10000 整、keeper 批 tx 0x3daf…04c0；私钥不出现场有专门测试锁死 |
| P1-2 | manifest 校验全分支、五步接入可走通、非法 manifest 拒绝、目录可被 SDK catalog 消费 | ✅ | core `4a5cb2c`/`4b35885`：providers 登记（ERC-8004 链上身份校验 404/409→422，负缓存）+《Provider 接入》五步对照 README；P0 期 manifest 校验全分支已在 core 25 用例内；目录被 SDK `catalog()`（ETag 协商）与演示脚本双重消费实证；范围纪律：未做第三方 Provider 预集成（发起人既定） |
| P1-3 | fixture 逐字段断言、空库优雅、**overview GMV 与链上 Charged 总额一致** | ✅ | core `e17ac58`：T22×11 unit + T23 live；Charged 增量水位索引（部署块−64 回补/≤5000 分窗/幂等）；B 交付时双路径核对 9 笔/10,080,000 一致，其后链上实时增长持续跟踪（收口时快照 30 笔/10.40，degraded=[]）；8010 新增 `/contracts/logs` 透传（发起人"缺端点先补端点"指示） |
| P1-4 | 剧本全程无人工干预跑通；友队掉线兜底切换演练通过 | ✅ | gateway `801c369`/`ac7e04e` + `results/demo_p1.md`：彩排 5m58s 零人工——第一幕 2.3s（≤30s 口径）、第二幕 402 指引→approve→三笔真实付费（预算计数=定价和）、第三幕 keeper 2 批 4×Charged=0.09 + providerWithdraw 到账 220000==提取额（tx 0xe61f…c76579）+ 排行榜 proof 可点 scan 核验；兜底演练：internal→友队 http_json 真实外联→kill 后 502 **零扣款实证**（spent_raw 不变/aborted+1）→备用端点→切回；切换传播 ≤60s TTL（诚实口径） |

## 质量门总账（P1 后）

| 仓 | 静态门 | 单测 | 覆盖率 | P1 tag |
|---|---|---|---|---|
| coincall-sdk | 绿（--no-cache 复核） | 41 unit + live + needs_funds | 90.58% | d0 |
| coincall-core | 绿 | 54 unit + 3 live | 96.36% | （本文提交打 p1） |
| coincall-gateway | 绿（主线程复跑） | 131 unit + live + needs_funds | 94.73% | —（d0/p0-e2e 既有） |
| coincall-bot-chain-api | 绿（停服务复跑 133 全过） | +9 unit + 1 live | 80.88% | —（d7-api-ext 后追加） |
| coincall-contracts | 未动（P1 无改动） | — | — | d0-contracts-r1 |

## 已知边界（诚实清单）

1. 三份独立实现（合约/网关/SDK）黄金向量互认成立——签名层无已知分歧；
2. 演示 runbook 铁律：**服务必须从主线程拉起**（子智能体会话进程会被回收，B/C 两轮实证）；8030 必须带 `COINCALL_KEEPER_ENABLED=true`；
3. C 抓到主线程任务书一处事实错：所给 anvil#2 私钥与地址不对应（派生 0xB4AD…79F），演示按派生地址全程一致执行并录档；0x3C44…93BC 历史 0.15 credits 无对应私钥未动；
4. 排行榜收入行 display_name=null 属展示缺口（agentWallet 与收款钱包天然分离，收入以 Charged 为真相）；
5. SDK 收据透传不本地验签、预算为进程内承诺（无裁判设计的固有属性）——README 诚实边界已明示；
6. 第四幕 Base Sepolia 同构（V1.5 可选线）未做，符合"先 BOT 链、保留可扩展性"的既定决策；
7. 彩排损耗：~0.13 USDT + 少量测试网 gas（含调试期 0.156 BOT 因 tx 漏 `to` 被当合约创建烧掉的教训，已修复并录档）。
