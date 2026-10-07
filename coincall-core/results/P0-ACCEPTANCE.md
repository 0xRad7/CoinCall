# P0 验收对账（W10，链 20261005202947）

> 对账基准：`coincall-docs/09_p0_p1_tasks.md` P0-1~P0-5 各节"验收"行。
> 结论：**五项全过**。日期：2026-10-05（本机）。发起人授权自动验收（INTENT §3）。

| # | 验收行（09 原文要点） | 结果 | 证据 |
|---|---|---|---|
| P0-1 | 身份端点可用：钱包绑定真实上链、注册结果可解析 | ✅ | coincall-bot-chain-api tag `d7-api-ext`：绑定 tx 实跑（gas 50416），register-result 从真实 tx 解析出 agentId=162；12/12 needs_funds 复跑绿；签名格式源码定案（EIP-712/newWallet 签名，CONSTRAINTS C-23 + results/w1_setagentwallet_findings.md） |
| P0-2 | PayVault 部署且不变量测试全绿 | ✅ | coincall-contracts tag `d0-contracts-r1`：合约 `0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471`（nonce 已回归 bytes32 正典），37 用例/88%，I1~I4 全覆盖；冒烟回环含重放拒绝与 I4=0 对账 |
| P0-3 | 骨架+四个冻结契约点 | ✅ | core（tag 前 HEAD 3f62078）：25 用例/98.58%；gateway tag `d0`：75→103 用例；manifest/payment/schemes/calls 四点冻结；**两侧黄金向量独立生成后交叉验证字节级一致**（gateway tests/test_payment_vectors_contract.py 3/3 PASS） |
| P0-4 | 网关跑通一次真实付费调用 | ✅ | gateway tag `p0-e2e` `results/e2e_p0.json`：manifest sha256:3fa6…→apikey key_3a221ea5→mint/approve 两 tx→**付费调用 200**（receipt rcp_7d2bcc9486fd，X-Charged-Raw=10000）；402 质询体 v2（payment 块） |
| P0-5 | keeper 批量上链+记账一致+续批不丢单 | ✅ | 批量结算 tx `0x06b3…4301d`：链上 3×Charged=30000 == 队列 done == calls settled（I4 对账，主线程独立解码复核）；e2e 单笔 30s 档自动结算 tx `0xddd5…f8dd` 且 nonce 对账；kill-重启续批单测覆盖（usedNonces 探测补记） |

## 质量门总账

| 仓 | 静态门 | 单测 | 覆盖率 | tag |
|---|---|---|---|---|
| coincall-bot-chain-api | 绿 | 124 unit + 48 live + 12 needs_funds | 80.36% | d7-api-ext |
| coincall-contracts | 绿 | 37 | 88% | d0-contracts-r1 |
| coincall-core | 绿 | 25 | 98.58% | （本文提交打 `p0`） |
| coincall-gateway | 绿（--no-cache 口径，C-08） | 103 unit + 1 live + needs_funds | 94.53% | d0 → p0-e2e |

## 已知边界（诚实清单）

1. 坏账拉黑仅在 gateway 进程内存（core 无 apikey 挂起端点，P2 已记 gateway CONSTRAINTS §E）；重启即清空。
2. 计价 token = MockUSDT（与 PayVault 部署绑定）；切真 USDT 需随合约重部署（决策留 P1）。
3. bot-chain-api：setMetadata/unsetAgentWallet ABI 已录未开端点（任务书允许砍）；indexer 水位追赶区间无上限（C-25）。
4. 质押型缺陷两起已结构性整改：nonce uint256（重部署 r1）、ruff 版本漂移+缓存掩盖（工具链钉死 + C-06/C-08 --no-cache 口径）。
5. 演示三幕串联脚本与 SDK/MCP（P1-1/P1-4）未动工——属 P1 范围，本链不含。
