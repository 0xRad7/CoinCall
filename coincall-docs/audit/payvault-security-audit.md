# PayVault 安全审计报告（上主网前把关）

> 审计日期：2026-10-01 · 审计人：ZCode 静态审计（AI）
> 审计对象：`coincall-contracts @ 当前工作区`（contracts/PayVault.sol、contracts/MockUSDT.sol）
> 目标链：BOT Chain 主网 chainId 677（测试网 968 已双纪元实证）
> 性质：**纯静态 + 本地 EVM 探针**，零链上操作（未发交易、未部署、未改仓内任何文件、无 git 操作）
> 关联文档：`coincall-docs/research/mainnet-readiness.md` §4（已知风险口径）、`coincall-docs/04_settlement.md` §2（规格与不变量 I1~I4）、`coincall-contracts/CONSTRAINTS.md`（铁律与偏差记录）

---

## 1. 审计范围与方法

### 1.1 范围

| 对象 | 说明 |
|---|---|
| `contracts/PayVault.sol`（155 行） | 核心结算合约：EIP-3009 同构链下授权 + `chargeWithSigBatch` 批量划扣 + `providerWithdraw` 提现 + `operatorUpdate` 轮换 |
| `contracts/MockUSDT.sol`（49 行） | 测试网/单测专用 ERC-20；审计重点 = 与真 USDT 的行为差异是否被测试掩盖 + 主网隔离是否可靠 |
| `payvault/`（eip712.py / chain.py / compile.py / networks.py） | EIP-712 参考实现、链上提交守卫、编译口径、网络表——作为理解佐证 |
| `script/deploy_testnet.py` | 网络驱动部署 + 冒烟 + 主网 dry-run guard——部署面与运维缓解证据 |
| `tests/`（5 文件 60 用例）、`vectors/eip712_golden.json` | 攻击面覆盖度与 EIP-712 字节级口径锁死证据 |
| `deployments/testnet-968.json` | 测试网部署事实（含冒烟全绿记录）——线上实证佐证 |

### 1.2 方法与工具结果（如实记录）

| # | 方法 | 结果 |
|---|---|---|
| M1 | **slither 静态分析**（uvx 临时环境，未入仓依赖） | **成功**。`uvx --from slither-analyzer slither`（slither 0.11.6；首次裸 `uvx slither-analyzer` 报需 `--from` 形态，改写后即通）。solc 二进制显式用 `~/.solcx/solc-v0.8.24 --solc` 指定（规避 PATH 上 anaconda solc 0.8.26 空壳风险，即 CONSTRAINTS C-02 沉淀）。**PayVault：12 项发现，全部 Informational 级（无 High/Medium）**：reentrancy-no-eth、missing-zero-check（constructor 两参 + operatorUpdate）、calls-loop、reentrancy-events×2、timestamp、costly-loop、low-level-calls×2、naming-convention（DOMAIN_SEPARATOR 大写）。逐项人工复核见 §3/§4。**MockUSDT：0 项**。未写任何文件入仓（`git status` 干净） |
| M2 | **人工逐条审查** | 全部完成，逐项结论 + 行号见 §3（发现）与 §4（核对表） |
| M3 | **本地 EVM 探针**（eth-tester，离线，与仓内单测同环境；探针脚本在 /tmp，未入仓） | 9 组探针 P1~P9 全部执行：可塑性孪生签名、transfer_failed 重试、空批、value=0、amount=0 提现、批量 gas（1/10/50 笔）、token=0 构造器伪记账、提现到任意第三方、空投破坏 I4。结果逐条并入 §3/§4 |
| M4 | **仓内测试全量回归** | `uv run pytest -q`：**60 passed**，10.32s，0 failed（分布：test_payvault_behavior 17 / test_networks_deploy 18 / test_eip712_golden 11 / test_chain_guards 10 / test_compile_artifacts 4） |
| M5 | **编译器已知问题核对** | solc 0.8.24（shanghai / optimizer 200 / legacy 非 via-IR 管线，compile.py:53-67 无 viaIR 设置）。对照 soliditylang 官方已知 bug 清单：影响 0.8.24 的共 4 项（SOL-2025-1 storage 数组 slot 溢出写丢失、SOL-2026-2/2026-4 互递归 spill 系（均 via-IR only）、SOL-2026-5 memory bytes 数组 delete 清整字）——**本合约无 storage 数组、无递归、非 via-IR、无 memory bytes delete，四项均不适用**。结论：无已知编译器 bug 在本合约形态下可触发 |
| M6 | 未做（超范围，如实声明） | 主网真 USDT（0xaBabc7…87a3C）源码复核（blacklist 语义——readiness §5-3 [待验证] 项，本审计只标记不替代）；模糊测试/形式化验证/第三方人工复核 |

---

## 2. 结论摘要

**总评：未发现 High/Critical 级缺陷。合约实现与其规格（04_settlement §2）一致，安全不变量 I1~I4 在代码与测试双层成立；测试网双纪元部署与冒烟实证了字节级行为。发现 1 项 Medium（签名不绑定收款方，属冻结契约的既定设计偏差）+ 5 项 Low + 5 项 Info，无一项构成"必须改代码再上主网"的阻断。**

**上主网判断：条件放行（conditional GO）。** 满足以下三个**运营前置**（均为非代码动作）即可按 runbook 部署：

1. **[F-01 缓解] keeper/网关对账监控上线**：Charged 事件与 settle_queue 逐笔（provider, from, value, nonce）比对告警（部署冒烟已有同口径先例 `charged_matches_queue`）；
2. **[F-02 缓解] 部署 runbook 增加非零断言**：`--operator` 与 token 参数非零地址（一行脚本侧断言；合约不可改，见 §3 F-02）；
3. **[F-07 缓解] 部署前完成主网 USDT 源码 blacklist/暂停语义复核**（readiness §6 阶段 1 已列，属既定动作）。

理由：合约 immutable 无升级面，任何代码修改 = 重部署新纪元 + 网关/keeper/SDK EIP-712 口径重接线（E-1/C-07 历史表明跨仓签名互认成本高）；而全部发现要么无实际影响（nonce 键控防御兜底），要么可由运营与监控等效缓解。**代价收益比不支持为 Nice-to-have 项改码重部署。** 同时必须知悉 §6 残留风险（operator 热钥、无 pause、合约未经第三方审计——发起人已在 readiness §4 知悉）。

发现总数 **11**：High 0 / Medium 1 / Low 5 / Info 5。必须处理再上主网（代码修复）**0 项**；运营前置 **3 项**（如上）。

---

## 3. 发现清单

> 处置分级口径：**A = 必须修再上主网**（0 项）；**B = 可带病上但要有运营缓解**；**C = 仅记录**。

### F-01 [Medium] 签名不绑定收款方：`ChargeCall.provider` 在 EIP-712 签名之外 —— 处置 B

- **位置**：`contracts/PayVault.sol:27-33`（ChargeCall struct，provider 与 v/r/s 平级、非 Authorization 成员）vs `:18-25`（Authorization 六元组无 payee 字段）；入账点 `:107`。
- **描述**：消费者签名只覆盖 `{from,to,value,validAfter,validBefore,nonce}`；`to` 被强制等于合约自身（`:92-94`），**实际收款 provider 完全由提交时的 keeper 决定**。对照 x402/EIP-3009 正典：收款人应进入签名承诺。这是 04 §2 规格 + 偏差 E-2/E-3/E-5 的**既定设计**（provider 记账键 = 地址，免 ERC-8004 依赖），不是实现走样。
- **影响**：operator 密钥误用/被劫持，或网关 settle_queue 数据被污染时，可把消费者支付记到错误 provider（消费者资金已按其签名金额离开钱包——损失面是"付错对象/付而未得"，不是超额划扣；超额划扣天然受消费者 `approve` 限额约束，I3）。另注意 `provider` 可以填 operator 自己（`tests/test_payvault_behavior.py:302` 即有一例 operator 作为 provider 入账——设计允许，仍受 credits 门槛约束）。
- **建议**：不改合约。运营缓解 = ①上主网前置 1 的事件-队列逐笔对账告警；②operator 热钥纪律（readiness §4.1 全套：keystore 托管、只存 gas、轮换演练）；③消费者侧文档诚实披露"支付路由由平台执行"。

### F-02 [Low] constructor 与 operatorUpdate 均无零地址校验 —— 处置 B

- **位置**：`contracts/PayVault.sol:64-66`（`token_`/`operator_` 直接赋值）、`:130-132`（`newOperator` 直接赋值）。slither missing-zero-check 同项。
- **描述**：① `token_ = address(0)`：本地探针 **P7 实证**——对空地址的 low-level call 成功且返回空数据，`_transferIn`（`:148` `ok && (ret.length == 0 || …)`）判为成功 → **凭空记账**（Charged 事件、totalCredits/credits 虚增）且"提现"同样伪成功（P7b）；I4 从部署起即失真。② `operator_ = 0` 或 `operatorUpdate(0)`：`msg.sender` 永不可能是 0 → 结算与轮换**永久死锁**（immutable 无恢复路径，P8 语义下提现不受影响，但平台停摆）。
- **可达性评估**：低。部署脚本对 token 有后置校验（`script/deploy_testnet.py:422-433` token_matches/operator_matches/domain 重建三项，不符 exit 2——但错合约已上链需弃置重部署）；网络表 token 为硬编码真 USDT。残余风险集中在 `--operator` 手工传参：传 `0x0` 时 `operator_matches` 是 0==0 会"通过"。
- **建议**：不改合约（immutable）。**部署 runbook/脚本加一行非零断言**（运营前置 2）；runbook 已有的部署后三项校验照跑。

### F-03 [Low] 签名可塑性：无 canonical-s / v 范围检查（已实证可过验签，无实际影响） —— 处置 C

- **位置**：`contracts/PayVault.sol:97-98`（ecrecover 结果只判 `address(0)` 与 `!= from`，无 `s ≤ n/2`、无 `v ∈ {27,28}` 显式检查）。
- **实证**（本地探针 P1 修正版，/tmp/payvault_probe3.py）：① high-s 孪生签名（`s' = n - s` 且 v 翻转 27↔28）配合**新 nonce** 直达验签步骤 → **Charged 成功**，即孪生签名确实通过合约 ecrecover；② 只取高 s 不翻 v、或 v ∈ {0,1,29} → `bad_signature`（ecrecover 返回 0 被 `:98` 捕获）。注意审计陷阱：合约检查顺序是 nonce(`:88`) **先于** 验签(`:97`)，用同 nonce 重放孪生签名只会得 `nonce_used`，不能作为可塑性证据——必须用新 nonce 探测。
- **影响**：**无**。重放防御是 nonce 键控（`usedNonces`）而非签名哈希键控，同一授权的两种签名形态共享同一 nonce，第二次必撞 `nonce_used`。可塑性仅在"以签名为唯一重放防线"的设计中才是漏洞。
- **建议**：仅记录。若未来重部署新纪元，可顺手加 `require(uint256(s) <= 0x7fff…5a5 << 1 …)` 形式的 canonical-s 检查作为纵深防御（OpenZeppelin ECDSA 同口径），当前不值得为它触发重部署。

### F-04 [Low] `ChargeFailed` 事件缺 nonce/value 字段，对账存在歧义 —— 处置 C

- **位置**：`contracts/PayVault.sol:45`（事件签名 `(provider, from, reason)`）、`:85-103`（五个 emit 点）。
- **描述**：同批同 (provider, from) 的多笔失败（尤其多笔 `transfer_failed`）从事件流无法区分是哪笔 nonce；value 也不在事件内。keeper 现行可凭自身队列顺序关联，但对账材料不自足。偏差 E-7 已冻结该口径。
- **建议**：仅记录。网关侧把队列（含 nonce）作为对账第一来源，事件为辅；未来新纪元可考虑 `ChargeFailed(provider, from, nonce, value, reason)`。

### F-05 [Low] I4 等式可被直接空投打破（balance > totalCredits），滞留资金无出口 —— 处置 C

- **位置**：`contracts/PayVault.sol:39-41`（totalCredits/credits/usedNonces，无 sweep 函数）、`:117-126`（唯一出口 providerWithdraw）。
- **实证**（探针 P9）：向金库直接 `transfer` 7 USDT 后 `balance=7e6, totalCredits=0`，等式向"多"的方向破裂，且该差额**永久滞留**（无 sweep、immutable）。不会导致少记/被盗（提现仍严格按 credits），但：① 以 `balanceOf == totalCredits` 为口径的 I4 监控会误报；② 误转入的用户资金无法找回。
- **建议**：仅记录 + **监控口径改为 `balance ≥ totalCredits` 且差额常数化告警**（差额变化 = 有人空投或出事）。

### F-06 [Info] 重入面：外部调用后写状态，但多层结构缓解 —— 处置 C

- **位置**：`contracts/PayVault.sol:102`（transferFrom 外部调用）之后才 `:106-108` 写 nonce/credits/totalCredits——slither reentrancy-no-eth 报告点；`providerWithdraw` 遵循 CEI（`:121-122` 状态更新先于 `:123` 外部转账，失败 revert 回滚状态）。
- **分析**：① `chargeWithSigBatch` 是 onlyOperator（`:151-154`），重入者 msg.sender=token 合约 ≠ operator，同函数重入直接 NotOperator；② `providerWithdraw` 被 token 回调重入时（msg.sender=token）需 token 自身持有 credits 才有意义，且 CEI 已保证余额单调扣减、无跨函数双花；③ 真 USDT（Tether 系）无 ERC-777 式钩子，回调路径不存在。合约刻意不引入 ReentrancyGuard（≤160 行 + USDT 无钩子前提），成立前提 = **token 永远是无钩子 ERC-20**——token 是构造器 immutable 参数，主网用官方 USDT，前提满足。
- **建议**：仅记录。前提（无钩子代币）写入运营认知：若未来切带钩子代币（777/ERC-1363），必须先重审本条。

### F-07 [Info→运营前置] 主网真 USDT blacklist/暂停语义未复核：金库被拉黑 = 系统性滞留 —— 处置 B

**[2026-10-08 复核完成——前置解除]** 经 scan.botchain.ai 已验证源码 + ABI 实测（只读）：主网 USDT（0xaBabc7…87a3C）为 AccessControl 风格合约，函数面仅 DEFAULT_ADMIN_ROLE/MINTER_ROLE/ERC20 标准 + burn/grantRole 等——**无 blacklist、无 pause、无 notBlacklisted 修饰器**。"金库地址被代币方拉黑→系统性滞留"的风险在该代币上不存在。残留仅：代币方 MINTER_ROLE 超发（治理风险，超出本平台控制面）。

- **位置**：合约层 `contracts/PayVault.sol:102-104`（transferFrom 失败 → `transfer_failed` 跳过，不炸批）与 `:123-124`（提现转账失败 → revert `WithdrawTransferFailed`）。
- **描述**：MockUSDT 无 blacklist/pause，测试网测试网 USDT 语义也未复核，主网 USDT（Tether 系克隆，readiness §5-3 [待验证]）大概率有。两条路径：①**消费者被拉黑** → 其 transferFrom revert → `transfer_failed` → keeper 两次重试后记坏账拉黑消费者（readiness §4.5，设计内路径，PASS）；②**PayVault 金库地址被拉黑/代币全局暂停** → 一切 charge 走 `transfer_failed`、一切提现 revert `WithdrawTransferFailed` → **资金滞留至解除**，合约层无任何缓解手段（也无 pause 权限可自保）。
- **建议**：不改合约。① **部署前读 scan.botchain.ai 已验证源码确认 blacklist/暂停函数与主体**（运营前置 3，runbook 阶段 1 已列）；② 上线后监控金库地址的 USDT 黑名单状态（若源码有 `isBlacklisted` 视图）；③ 平台行为合规红线：不触碰可能致金库被拉黑的业务。

### F-08 [Info] 边界行为记录（全部实测，均为可接受语义） —— 处置 C

- **位置/实证**：① 空批 `[]` → no-op 成功（探针 P3，gas 23,943）；② `value=0` 授权 → Charged(value=0) 且烧 nonce（探针 P4；黄金向量 case3 也有 value=0）；③ `amount=0` 提现（credits>0）→ 成功不动账（探针 P5 + 既有测试 `test_withdraw_zero_amount_with_zero_credits_allowed`）；④ 时间窗含等号（`:84` `>` 拒绝 → validBefore 当秒仍有效，与 EIP-3009 正典一致）；⑤ timestamp 依赖（slither timestamp 项）：validator 可拨 ±数秒，支付时间窗边界同幅度漂移——金额/价格不依赖 timestamp，接受；⑥ 提现 `to` 可为任意第三方地址（`:117-118` 仅拒零地址；探针 P8 到账 3 USDT 实证）——设计如此（E-3，缺省建议=agentWallet），注意打错地址不可逆。

### F-09 [Info] 批量 gas 实测：50 笔满批 ~1.68M gas，远低于常规区块上限 —— 处置 C

- **实证**（探针 P6，eth-tester）：单笔批 86,559 gas；10 笔 405,744（边际 ~40.6k/笔）；50 笔 1,684,526（边际 ~33.7k/笔，含跳过笔更便宜）。`MAX_BATCH=50`（`:15,:81`）+ 51 笔 revert（测试 `test_max_batch_enforced`）。keeper 默认攒批 3 笔（04 §3），距上限 16 倍余量；主网 677 区块 gas 上限未实测，但 2M 级交易在 15M+ 常规上限内无塞爆风险（**待验证**项仅剩主网区块上限本身，属常规部署核验）。

### F-10 [Info] MockUSDT 与真 USDT 行为差异及主网隔离 —— 处置 C

- **掩盖点**：MockUSDT 恒返回 `bool true`（`contracts/MockUSDT.sol:23-40`），**掩盖了合约为非标准 USDT 写的兼容分支**——`_transferIn` 的 `ret.length == 0` 判成功（`:148`）与 providerWithdraw 的同款（`:124`）从未被任何测试走过；MockUSDT 无 blacklist/暂停（F-07 ② 的系统性场景测试网测不到）；`mint` 无权限开放（`:18-21`，仅测试网）。
- **对齐点**：6 decimals（部署预检 `symbol/decimals` 断言，deploy_testnet.py:376-386）；无限授权不减额（`:36-38`，与 Tether 语义一致）；transferFrom 失败 revert 而非 return false（同 Tether）。
- **主网隔离核查（PASS）**：`run_mainnet` 只部署 PayVault（deploy_testnet.py:415-417，代码路径上不存在 MockUSDT 部署调用）；主网部署产物 `abiFiles` 不含 mockUsdt（`:455`）；测试 `test_mainnet_flow_has_no_anvil_mnemonic` 结构性拦测试助记词进主网流程。隔离靠脚本结构而非独立机制——结论：够用，但"绝不上主网"是流程约束，建议 runbook 阶段 1 加人工核对：主网部署后 scan 上**不存在**名为 "Mock USDT" 的新合约。

### F-11 [Low] `transfer_failed` 后原签名重试路径无回归测试 —— 处置 C（补测试）

- **位置**：`contracts/PayVault.sol:77-78`（NatSpec 声明"nonce 仅在扣款成功后烧毁，坏账笔可携原签名重试"）+ `:102→:106`（烧毁点在 transfer 成功之后）。
- **实证**（探针 P2）：余额不足 → `transfer_failed`，nonce 未烧（usedNonces=false）；补余额后**同一签名重提 → Charged(5 USDT)**。NatSpec 声明为真，但仓内 60 用例无一覆盖（T6 的 transfer_failed 只验证跳过，未验证重试）。这是 keeper 坏账重试机制的合约基石，应有回归防未来重构破坏。
- **建议**：仅记录 + 按 §5 T-G2 补测试。

---

## 4. 攻击面核对表

| # | 审查项 | 结论 | 证据（一句） |
|---|---|---|---|
| 1 | operator 权限面 | **PASS** | 仅 `chargeWithSigBatch`/`operatorUpdate` 两个 onlyOperator 入口（PayVault.sol:79,130,151-154）；ABI 冻结清单测试无多余函数（test_p8_abi_surface_has_no_admin_locks）；operator 亦可作为 provider 收款（test:302），仍受 credits 门槛约束 |
| 2 | owner/admin 后门 | **PASS** | 无 owner/admin/renounce 类函数；constructor 只设 token/operator/DOMAIN_SEPARATOR（:64-72）；测试结构性证明 ABI 面无管理锁 |
| 3 | constructor 零地址校验 | **FAIL** → F-02 | `:65-66` 直接赋值无校验；token=0 伪记账已实证（P7）、operator=0 永久停摆 |
| 4 | EIP-712 重放防护（nonce 单次销毁） | **PASS** | `usedNonces` 键控（:88 检查/:106 烧毁）；跨交易重放 T5 测试 + 部署冒烟 nonce_replay_rejected 双实证 |
| 5 | nonce 烧毁时机/位置 | **PASS（设计内）** | 仅 transfer 成功后烧（:102→:106），失败笔可重试（NatSpec :77-78，P2 实证）；USDT 无钩子 → 检查与烧毁间无重入窗口 |
| 6 | 有效期语义（validBefore 含等号？） | **PASS** | `:84` 用 `>` 拒绝 → validBefore/validAfter 均**含**当日秒，与 EIP-3009 正典一致；过期/未到窗测试（test_expired_window_skipped） |
| 7 | 金额绑定 | **PASS** | value 在签名 struct 哈希内（:139），验签覆盖；金额篡改 → bad_signature |
| 8 | 收款方绑定 | **FAIL（设计偏差）** → F-01 | auth.to 强制本合约（:92-94）但 provider 在签名外（:27-33） |
| 9 | ecrecover 0 地址 | **PASS** | `:98` `recovered == address(0) || recovered != from` → bad_signature；v∈{0,1,29} 实证全部拒绝（P1 修正版） |
| 10 | 签名可塑性（s 范围/v∈{27,28}） | **PARTIAL** → F-03 | 无检查，high-s 孪生实证可通过验签（P1 修正版 Charged=1）；但 nonce 键控使可塑性零影响 |
| 11 | 批量原子性（坏笔 vs 整批） | **PASS** | 单笔失败 `continue` + ChargeFailed 短码（:85-104），仅 BatchTooLarge 整批 revert（:81）；T6 三笔混合批实证 |
| 12 | decimals/数值溢出 | **PASS** | 0.8.24 checked arithmetic（:107-108,:121-122 溢出即 revert）；合约无小数运算，value 原值记账（6 decimals 由 token 层承担） |
| 13 | 同批次重复 nonce | **PASS** | 批内第二笔撞 `:88` → nonce_used（test_duplicate_nonce_within_same_batch） |
| 14 | credits 记账一致性 | **PASS** | 记账值 == 扣款值（同 `c.auth.value` 源，:102/:107）；整数无舍入 |
| 15 | 舍入/粉尘 | **PASS/N-A** | 全整数运算，合约无除法 |
| 16 | providerWithdraw 清零路径 | **PASS** | `credits = available - amount`（:121），可部分可清零（T7 实测 (10→6→3) 全程 I4 成立） |
| 17 | 重入（token.call 模式 + 无 ReentrancyGuard） | **PASS（带 F-06 注记）** | providerWithdraw 严格 CEI（:121-123）；charge 批量 onlyOperator 阻断重入；USDT 无钩子；外部调用后 emit 事件（slither reentrancy-events）无资金影响 |
| 18 | operator 任意转出资产（I1） | **PASS** | 合约仅两处 token 调用：_transferIn 目标恒 address(this)（:146）、providerWithdraw 由 msg.sender 的 credits 门槛约束（:117-124）；T4 双测试（operator 0 credits 提现拒 + 伪造签名零变动） |
| 19 | 余额==Σcredits 不变式（I4） | **PASS（方向注记 F-05）** | totalCredits 增减闭环（:108/:122）；T7 + 部署冒烟 i4_after_charge/after_withdraw 实证；空投可破"大于"方向（P9） |
| 20 | 事件可观测性 | **PASS（缺口 F-04）** | Charged 四元组支撑 keeper 逐笔对账（冒烟 charged_matches_queue）；Withdrawn/OperatorUpdated 完备；ChargeFailed 缺 nonce |
| 21 | 批长度上限 | **PASS** | MAX_BATCH=50（:15,:81），51 笔 BatchTooLarge revert（测试） |
| 22 | gas 塞爆 | **PASS** | 50 笔满批实测 1.68M gas（P6/F-09），远低于区块上限；keeper 默认批 3 |
| 23 | USDT 黑名单/暂停时 charge 失败路径 | **PASS（设计内）** | 消费者拉黑 → transfer_failed 跳过不炸批（:102-104）→ keeper 重试→坏账拉黑（readiness §4.5）；金库拉黑 = F-07 系统性残留 |
| 24 | 编译器版本已知问题（0.8.24） | **PASS** | 官方 bug 清单 4 项涉及 0.8.24，全部不适用本合约形态（M5：无 storage 数组/无递归/非 via-IR/无 memory bytes delete） |
| 25 | proxy/升级性/init 函数 | **N-A（PASS）** | 无 proxy 无 initializer，token/operator/DOMAIN_SEPARATOR 均 immutable（:37-38,:64-72）；修复 = 重部署新纪元（部署脚本双纪元机制已具备） |
| 26 | EIP-712 字节级口径 | **PASS** | Python 参考/合约/黄金向量三层逐字节一致（T8 共 11 用例 + test_domain_separator_matches_python）；typehash 字符串双仓字面一致（eip712.py:25-29 vs PayVault.sol:56-60） |
| 27 | 跨链/跨合约签名重放 | **PASS** | DOMAIN_SEPARATOR 含 chainId+verifyingContract（:69），968/677 域不同 + to==address(this) 双保险；跨域字面签名测试拒绝（test_cross_domain_signature_rejected） |
| 28 | MockUSDT 主网隔离 | **PASS（结构性）** | run_mainnet 代码路径只部署 PayVault（deploy_testnet.py:415-417）、产物 abiFiles 排除 mockUsdt（:455）；建议 runbook 加人工核对（F-10） |

---

## 5. 测试覆盖缺口与建议补充用例

**已覆盖的攻击面**（60 用例，全绿）：权限（NotOperator 两处）、批量上限、正常路径+事件四元组、I1 双路径、nonce 重放（跨交易+批内）、混合批跳过（bad_signature/transfer_failed）、时间窗（过期+未到）、to 劫持、I4 全程（充值/提现/坏批）、提现边界（部分/清零/超额/零地址/零额）、operator 轮换（新旧权限切换）、P8 ABI 冻结、产物防漂移、编译器钉版、EIP-712 三层（向量/恢复/合约消费/跨域拒绝）、链守卫（域白名单/代理/chainId/20gwei/密钥）、网络表快照、主网密钥纪律与 dry-run guard。

**缺口与建议补充**（探针 P 系列已在本地 EVM 原型验证可行，可低成本移植入 `tests/`；本审计未改动 tests/）：

| # | 缺口 | 建议用例 | 探针状态 |
|---|---|---|---|
| T-G1 | 签名可塑性无回归 | high-s 孪生（新 nonce）可 Charged + 同 nonce 重放孪生 → nonce_used；v∈{0,1,29} → bad_signature | P1 修正版已原型 |
| T-G2 | transfer_failed 重试路径无测试 | 余额不足 → transfer_failed 且 usedNonces=false → 补余额后同签名重提 → Charged | P2 已原型 |
| T-G3 | 非 bool 返回 token 的兼容分支（`:148`/`:124` ret.length==0）零覆盖 | 写一个不返回数据的 mock token，断言 charge/withdraw 被判成功（USDT 兼容分支的存在性证明） | 未做（需新 mock 合约，建议入 tests 夹具） |
| T-G4 | 黑名单 token 行为 | mock 加 blacklist：拉黑消费者 → transfer_failed 跳过；拉黑金库 → 提现 revert WithdrawTransferFailed（把 F-07 两条路径固化为测试） | 未做 |
| T-G5 | value=0 / amount=0 边界 | value=0 授权 Charged(0)+烧 nonce；credits>0 时 amount=0 提现成功不动账 | P4/P5 已原型 |
| T-G6 | 空批行为 | `chargeWithSigBatch([])` 成功 no-op | P3 已原型 |
| T-G7 | 满 50 笔成功批 + gas 基线 | 50 笔全 Charged，记录 gasUsed 基线防回归（当前 ~1.68M） | P6 已原型（含 nonce 撞车噪音，移植时用互异 nonce） |
| T-G8 | 恶意 token 重入探针（可选纵深） | 带钩子 token 在 transferFrom 中重入 chargeWithSigBatch/providerWithdraw，断言 NotOperator/InsufficientCredits | 未做（低优先级，F-06 前提已立） |

---

## 6. 已知接受的残留风险（发起人知悉，对照 mainnet-readiness §4）

1. **operator 热钥（§4.1）**：`chargeWithSigBatch` onlyOperator、单步轮换、无 pause/无时间锁——合约层如实无锁（本审计 §4 #1/#25 证实）。热钥失窃 = 可在消费者 approve 限额内任意划扣 + 可错误指定 provider（F-01 放大滥用面）。缓解按 readiness §4.1 全套执行（keystore 托管、0600、只存 gas、轮换演练）；服务端唯一额度防线 wallet_daily_cap **严禁置 0**（§4.2）。
2. **无 pause 的双刃（§4.1/铁律 P8）**：提现永开（资金主权保障）同时意味着**任何错误批量上链不可逆、无止损开关**；消费者唯一防线 = 自主 approve 限额 + 网关 cap。
3. **合约未经第三方审计（§4.8）**：本报告为内部 AI 静态审计，不替代第三方专业审计；发起人以"消费者 approve 自控 + P8 + 小额起步"为既有缓解，知悉接受。
4. **providerWithdraw 需 BOT 付 gas（§4.4）**：只有 USDT 无 BOT 的 provider 提不了现——帮助文档义务（非合约缺陷）。
5. **主网 USDT 语义（§5-3 [待验证]）**：F-07——部署前源码复核为前置；金库被拉黑 = 资金滞留无合约出口。
6. **主网 677 链假设**：20 gwei 恒定、PUSH0/shanghai、区块 gas 上限、getLogs 窗口——均按测试网 968 口径假设，runbook 各阶段已有试探动作；本审计未上链验证（红线 §0）。
7. **MockUSDT 差异掩盖（F-10）**：非 bool 返回/黑名单分支测试网永远测不到，靠 T-G3/T-G4 补齐 + 部署前源码复核兜底。

---

## 附：审计执行痕迹（可复现）

```text
# slither（uvx 临时环境，~/.solcx/solc-v0.8.24 显式指定）
uvx --from slither-analyzer slither contracts/PayVault.sol  --solc ~/.solcx/solc-v0.8.24   # 12 results, 全 Informational
uvx --from slither-analyzer slither contracts/MockUSDT.sol --solc ~/.solcx/solc-v0.8.24   # 0 results

# 全量测试
uv run pytest -q    # 60 passed in 10.32s

# 本地 EVM 探针（eth-tester 离线；脚本未入仓）
PYTHONPATH=. uv run python /tmp/payvault_probes.py    # P1~P6（P1 后被修正版取代）
PYTHONPATH=. uv run python /tmp/payvault_probes2.py   # P7~P9
PYTHONPATH=. uv run python /tmp/payvault_probe3.py    # P1 修正版：可塑性/非法 v 合约级实证
```

关键探针结果：孪生签名(新 nonce) Charged=1；高 s 不翻 v / v∈{0,1,29} → bad_signature；transfer_failed 后同签名重试 Charged；空批 no-op；value=0 Charged(0)；token=0 金库伪记账+伪提现；空投后 balance(7e6) > totalCredits(0)；50 笔批 1,684,526 gas。
