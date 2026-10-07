# 主网就绪调研：CoinCall 切换 BOT Chain 主网（677）

> 2026-10-01 · 调研产物（只读代码 + 链上实测，未改任何代码/配置/服务）。
> 来源标注：**[实测]** = 本次经本机代理直接对主网 RPC/浏览器 API 发起只读请求核实；
> **[本地]** = 仓库内文档/代码事实；**[尽调]** = BOT_CHAIN_REPORT.md（2026-09-29 尽调快照）；**[待验证]** = 未能核实。

---

## 0. 红线：主网模式下 AI 助手的操作边界（2026-10-07 发起人裁定）

- 切到主网后，**AI 助手不得自主发起任何链上写操作**——包括但不限于：合约部署、转账、
  approve、chargeWithSigBatch、身份注册交易。一切上链动作由发起人手动执行并核验。
- AI 助手允许：只读链上查询（eth_call/getLogs/余额）、代码与配置准备、脚本编写、
  事后对账分析。
- 任何"顺手验证一下"都不构成例外；宁可慢，不可未经批准上链。

---

## 1. 主网基础事实

| 项 | 值 | 状态 |
|---|---|---|
| chainId | **677**（eth_chainId=0x2a5） | [实测] |
| RPC | `https://rpc.botchain.ai/`（本机直连超时，**经代理 http://127.0.0.1:7890 可达**） | [实测] |
| gasPrice | **20 gwei**（eth_gasPrice=0x4a817c800，与测试网恒 20 gwei/baseFee=0 模型相同） | [实测]（单点时刻，长期稳定性见 §5） |
| 区块浏览器 | `https://scan.botchain.ai`（Blockscout，`/api/v2` 开放免 key） | [实测] API 返回正常 |
| 原生币 | **BOT**，18 decimals；供应 150M、币价 ≈$12.37 | 币名/精度 [本地]；价与供应 [尽调] 快照，时效存疑 |
| 主网 USDT | `0xaBabc7Ddc03e501d190C676BF3d92ef0e6e87a3C`：symbol=USDT、decimals=6、浏览器名 Tether USD、301,353 持有者、总供应 73,960,200 | [实测] **已部署、活跃，无需自部署** |
| IdentityRegistry（ERC-8004） | `0xB43Edfb9C7609cF645e932B2fF20f26F0d4488dE`：有代码，ERC-1967 代理形态（bytecode 含 EIP-1967 impl slot） | [实测] **官方已部署，无需自部署** |
| 4337 Bundler | `https://bundler.botchain.ai/rpc/`（EntryPoint v0.7 两网同址单例） | [本地] 未实测；CoinCall 结算链路不依赖（走 EOA+keystore） |
| 主网领水 | `faucet.botchain.ai/basic` 返回 HTTP 200 | [实测] 页面存在；**主网发放真实币的机制存疑**，不可依赖 [待验证] |
| BOT 获取途径 | bridge.bohr.life（USDT 桥：ETH/BNB/Tron）+ dex.botchain.ai 兑换 | [尽调]；CEX 上线情况 [待验证] |

网络可达性要点 [尽调]：`botchain.ai` 整域国内 DNS 污染；`*.bohr.life` 正常。主网一切操作（RPC/浏览器/DEX）在污染环境必须走代理。

## 2. 合约层清单

### 2.1 需要部署：仅 PayVault 一个

- 现状 [本地]：`coincall-contracts/deployments/testnet-968.json`（epoch2，operator=0xC37f…B63a，测试网）。主网无任何自有合约。
- **USDT / IdentityRegistry / Reputation / Validation 均为官方既有合约**，主网地址已在 `coincall-bot-chain-api/app/core/chains.py` 配好，只换配置不部署。
- **部署署名者**：一个持有主网 BOT 的 EOA（可冷钥手动管理，仅用于部署）。PayVault 构造器 `PayVault(token, operator)`——deployer 与 operator 可分离。
- **operator 地址的确定方式（关键约束）**：bot-chain-api 的 Keystore 只有 `create()` 没有 import（`app/core/keystore.py`），且主网模式拒载 env 私钥（`app/core/config.py:funded_key`）。因此 operator 必须是 **keystore 里新建的账户**：主网模式起服务 → `POST /api/v1/accounts`（reveal 一次可选）→ 记下地址 → 外部向该地址转入 BOT（gas）→ 部署 PayVault 时 operator=该地址。
- **部署脚本改造** [本地]：`script/deploy_testnet.py` 硬编码 968/测试 USDT/anvil 助记词冒烟，`payvault/chain.py` 的 `connect_testnet/assert_chain_id(968)` 同理——需要参数化网络（RPC+chainId 677+主网 USDT）或另写 `deploy_mainnet.py`；冒烟金额压到最小（真金）；署名前 chainId==677 断言保留。
- **部署产物**：`deployments/mainnet-677.json`（新纪元文件，不覆盖 testnet-968.json）；core/gateway/console/SDK 全部以它为唯一事实源换地址。
- **合约源码验证**：scan.botchain.ai（Blockscout）标准验证流程，solc 0.8.24 [本地] 编译口径不变。
- **domainSeparator 变化**：EIP-712 域含 chainId+verifyingContract——主网新金库的 DOMAIN_SEPARATOR 必须用 677+新地址重建（黄金向量 `vectors/eip712_golden.json` 仅锁 name/version/structType，不含地址，可复用）。
- MockUSDT 绝不部署主网 [本地]（README 诚实边界）。

### 2.2 需要重新确定的地址/配置

| 项 | 测试网现值 | 主网动作 |
|---|---|---|
| PayVault | 0xa6E8…9ff0 | 部署后新地址 |
| USDT | 0x75ed…0fe3 | 0xaBabc7…87a3C [实测] |
| IdentityRegistry | 0xec8f…99c0 | 0xB43E…488dE [实测] |
| operator | 0xC37f…B63a（测试键） | keystore 新建主网账户 |
| platform custodian | 0xc37f…b63a | 同主网出资/托管账户（keystore 同源） |

## 3. 四个服务的配置变更清单

### 3.1 coincall-bot-chain-api（8010）——零代码，仅 .env

[本地] 主网规格已全部配好（chains.py），双重锁机制（铁律 A1）：

```
BOT_CHAIN_NETWORK=mainnet
BOT_CHAIN_ALLOW_MAINNET=1        # 缺此项启动即报 mainnet_locked
PROXY=http://127.0.0.1:7890      # Docker 内用 http://host.docker.internal:7890
BOT_CHAIN_KEYSTORE_SECRET=<强随机口令>   # 不设则代管账户仅内存态，重启即丢（operator 键！）
```

- 测试私钥环境变量（`TEST_PRIVATE_KEY` 一族）主网模式拒载（误填不生效），写接口仅 dry_run。
- 自动切换面：rpc/bundler/explorer/faucet 四客户端与合约地址切主网；DuckDB 六表按 network 列隔离两网数据 [本地]。
- 健康验证：`curl /api/v1/chain/health`——rpc 通道 chainId 必须 677，红灯即误配。

### 3.2 coincall-gateway（8030）——env（前缀 COINCALL_）

| env | 主网值 |
|---|---|
| COINCALL_PAY_VAULT_ADDRESS | <主网新金库> |
| COINCALL_PAYMENT_TOKEN_ADDRESS | 0xaBabc7Ddc03e501d190C676BF3d92ef0e6e87a3C |
| COINCALL_CHAIN_RPC_URL | https://rpc.botchain.ai/ |
| COINCALL_CHAIN_ID | 677 |
| COINCALL_KEEPER_OPERATOR_ADDRESS | <主网 operator（keystore 账户）> |
| COINCALL_IDENTITY_REGISTRY_ADDRESS | 0xB43Edfb9C7609cF645e932B2fF20f26F0d4488dE |
| COINCALL_KEEPER_ENABLED | true（up.sh 已带） |

**[代码缺口]** keeper 的 `_fetch_receipt` 自建 `AsyncWeb3(AsyncHTTPProvider(rpc))`（`app/modules/keeper.py:245`）**无代理支持**——DNS 污染环境下主网回执读取会挂。需要：补代理支持，或部署在 botchain.ai 可直连的环境。这是主网切换前必须解决的代码项。

### 3.3 coincall-core（8020）——env（前缀 COINCALL_CORE_）

| env | 主网值 |
|---|---|
| COINCALL_CORE_PAY_VAULT_ADDRESS | <主网新金库> |
| COINCALL_CORE_PAY_VAULT_DEPLOY_BLOCK | <主网部署块号>（Charged 回补起点；不改会用 968 的 25870488 在 677 上错扫） |
| COINCALL_CORE_PLATFORM_CUSTODIAN_ADDRESS | <主网托管地址> |

- 建议主网换新 DuckDB 文件（COINCALL_CORE_DUCKDB_PATH）——core/gateway 的库无 network 列隔离（那是 bot-chain-api 六表的设计），混库会让测试网 Charged 水位污染主网排行榜。
- bot-chain-api 的 DUCKDB_PATH 同理建议分文件（虽有 network 列，分库更干净且规避 C-14 单写者锁）。

### 3.4 coincall-console（5173）——源码常量（需改代码重新构建）

`src/chain/constants.ts`：CHAIN_ID 677、CHAIN_NAME、RPC_URL、EXPLORER_TX→scan.botchain.ai、PAY_VAULT/USDT/IDENTITY_REGISTRY 换主网、FAUCET_URL→改为主网资金获取指引（桥/兑换）；GAS_PRICE_GWEI=20 不变 [实测]。
`src/chain/injected.ts`：BOT_CHAIN_PARAMS（chainName "BOT Chain"、rpcUrls、blockExplorerUrls）与 ensureChain968 的链校验（0x2a5）；`src/chain/signing.ts`:18 CHAIN_ID=968→677（EIP-712 域）。
帮助页（Help.tsx 等）领水文案全部改写。
**注意**：浏览器端用户直连主网 RPC——污染地区终端用户钱包/RPC 同样受影响（钱包内置 RPC 或用户自代理是用户侧问题，需在帮助页说明）。

### 3.5 coincall-sdk（消费者侧，第 5 个要动的地方）

`coincall/signing.py`:26 `CHAIN_ID=968` 硬编码；`coincall/chain.py`:94 组装 tx 前**断言 chainId==968**；`coincall/wallet.py` 默认 PAY_VAULT_ADDRESS/CHAIN_ID。需参数化（或出 677 版）并**保留"签名前断言==目标链"防线**，发布新版通知消费者升级。

### 3.6 重启顺序

先停全部旧实例（up.sh 纪律：端口占用会静默失败）→ bot-chain-api → core → gateway → console。core/gateway 依赖 8010 链通道，console 依赖三后端。

## 4. 安全与运营注意（主网真金增量项）

1. **operator 热钥**：`chargeWithSigBatch` 是 onlyOperator、`operatorUpdate` 单步轮换、合约无 pause/无 owner 锁/无时间锁 [本地合约仓]。热钥=可在消费者 approve 额度内任意划扣。缓解：operator 账户 keystore 生成（私钥仅 reveal 一次）、`.env`（KEYSTORE_SECRET）chmod 0600 永不入库、账户只存 gas 所需 BOT 不囤 USDT、轮换演练（新 keystore 账户→部署时用新 operator→旧账户清空）。
2. **wallet_daily_cap 复核**：网关咽喉卡口默认 50 USDT/日/钱包（`coincall-gateway/app/core/config.py`）。主网初期建议维持或调小，**严禁置 0**（=关闭）。这是绕过 SDK 直打网关也绕不过的唯一服务端额度防线。
3. **私钥纪律**：.env 0600 + .gitignore；日志脱敏已有（bot-chain-api core/log.py 强制）；deploy script 私钥只从 env 读 [本地]。
4. **providerWithdraw 权限**：provider 自提自账（msg.sender），平台无法代提也无需多签——但 provider 提现需要 BOT 付 gas，帮助文档必须写清（用户只有 USDT 没 BOT 会提现失败）。
5. **blacklist 机制**：keeper 对 transfer_failed 两次重试后记 bad_debt 并拉黑消费者 [本地 keeper.py]。主网真实坏账走此路径，需监控黑名单增长与坏账额。
6. **监控对账**：① I4 不变量 `USDT.balanceOf(PayVault) == totalCredits()`（充值/提现后各校验的既有口径）；② core 排行榜 Charged 链上索引 vs gateway settle_queue 状态对账；③ `/api/v1/chain/health` 677 绿灯探测；④ keystore 账户 BOT 余额低水位告警（keeper gas 耗尽=结算停摆）。
7. **生产密钥**：gateway 的 `receipt_secret`、core 的 `credential_secret` 均 dev 占位默认值，主网部署必须 env 注入强随机。
8. **合约未审计** [本地诚实边界]：额度由消费者 approve 自控 + 提现路径永开 + 小额演示是既有缓解，主网维持。

## 5. 风险与未知项

| # | 风险/未知 | 说明 | 建议 |
|---|---|---|---|
| 1 | **DNS 污染链路缺口（最大工程风险）** | bot-chain-api 有 PROXY 分流；**gateway keeper 的 web3 直连与 console 浏览器端 RPC 均无代理** | keeper 补代理支持（代码项）；console 帮助页说明；或整体部署在可直连 botchain.ai 的环境 |
| 2 | 主网 gas 模型长期稳定性 | 单点实测 20 gwei；POA 恒定是测试网口径，主网未长期观察。若动态化，console/SDK/bot-chain-api 的 20 gwei 假设需复查 | 切换后持续观测 eth_gasPrice；低成本（20gwei×~140k gas≈0.0028 BOT/笔） |
| 3 | 主网 USDT 合约语义 | [待验证] 是否带 blacklist/暂停等 Tether 式管控——若官方 USDT 有黑名单，被拉黑消费者的 transferFrom 会走 transfer_failed 坏账路径 | 切换前读 scan.botchain.ai 已验证源码确认 |
| 4 | 主网 BOT 获取途径 | faucet 页面存在但发真币机制存疑；桥（bridge.bohr.life）与 DEX 路径 [尽调] | 实际走一遍小额入金验证；CEX 上线情况查证 |
| 5 | 主网 getLogs 窗口上限 | core Charged 同步窗口按测试网 5000 块口径 | 切换后小窗试探（500 起步） |
| 6 | 主网 bundler 未实测 | CoinCall 不依赖（结算走 EOA） | 无需阻塞 |
| 7 | 币价/供应数据时效 | [尽调] 2026-09-29 快照 | 引用时重新拉浏览器 API |
| 8 | explorer API 主网限流策略 | /api/v2 开放但限流未知 | 上量前压测 |

## 6. 切换 runbook 草案

```text
阶段 0 · 前置（代理与资金）
[ ] 确认部署环境 botchain.ai 可达（本机：代理 7890 实测 eth_chainId=0x2a5）
[ ] 准备部署用 EOA（冷钥）并注入主网 BOT（经桥/DEX/CEX，走通一次入金）
[ ] gateway keeper 代理支持代码项落地（§5-1，唯一必改代码；其余全为配置/常量）

阶段 1 · 合约（coincall-contracts）
[ ] 部署脚本参数化或 deploy_mainnet.py（chainId 677 断言 + 主网 USDT + 最小额冒烟）
[ ] 部署 PayVault(token=主网USDT, operator=<占位，阶段 2 生成 keystore 地址后再定>)
    —— 实操顺序：先起主网 bot-chain-api 生成 operator（阶段 2 步骤 1-3），再回来部署
[ ] 冒烟最小集：未授权 charge 拒绝 + 1 笔最小额真金 Charged + I4 对账（省略 anvil 垫付剧情）
[ ] 写 deployments/mainnet-677.json；scan.botchain.ai 验证合约源码
[ ] 复核主网 USDT 源码（blacklist 语义，§5-3）

阶段 2 · bot-chain-api（8010）
[ ] .env：BOT_CHAIN_NETWORK=mainnet + BOT_CHAIN_ALLOW_MAINNET=1 + PROXY + KEYSTORE_SECRET(强随机)
[ ] 停旧实例（up.sh stop / kill）→ 起新实例
[ ] curl /api/v1/chain/health → chainId=677 绿灯
[ ] POST /api/v1/accounts 生成 operator 账户（reveal 一次确认备份提示）
[ ] 向 operator 地址转 BOT（gas 储备，低水位告警阈值定好）
[ ] （回到阶段 1 完成 PayVault 部署，operator=该地址）

阶段 3 · core（8020）
[ ] .env：PAY_VAULT_ADDRESS/PAY_VAULT_DEPLOY_BLOCK(主网部署块)/PLATFORM_CUSTODIAN_ADDRESS
[ ] 新 DUCKDB_PATH（空库起步）
[ ] 重启 → /healthz 200 → 排行榜触发一次 Charged 同步（小窗试探 getLogs 上限）

阶段 4 · gateway（8030）
[ ] .env：§3.2 全表 + KEEPER_ENABLED=true + receipt_secret 强随机
[ ] 新 DUCKDB_PATH → 重启 → /internal/keeper/status enabled + queue 正常

阶段 5 · console + SDK
[ ] 改 src/chain/{constants,injected,signing}.ts（§3.4）+ 帮助页资金指引（桥/兑换/gas 说明）
[ ] 重新构建部署
[ ] SDK 参数化 CHAIN_ID/PAY_VAULT（保留签名前链断言）→ 发版通知消费者

阶段 6 · 端到端验证（真金小额）
[ ] 消费者钱包切 677 → 持主网 USDT + BOT → approve PayVault 最小额
[ ] SDK/网页发起一次付费调用 → settle_queue → keeper 上链 → Charged 事件核对
[ ] provider providerWithdraw 到账核对（I4 前后对账）
[ ] 错误路径演练：超额调用被 cap 拦截；_keeper 重启续批_用例（pending + usedNonces 探测）
[ ] 监控项上线：I4 对账定时、健康探测、operator 余额告警、黑名单/坏账看板
```

---

### 结论速览

- **主网最大 3 个变更点**：① operator 签名路径重构——env 私钥拒载 + keystore 无 import，主网 operator 必须「keystore 新建账户+事后注资」，keeper 才能上链；② PayVault 主网重部署并驱动 gateway/core/console/SDK 四处地址与 chainId 全量换血（core 部署块、SDK 链断言、console 三文件）；③ 网络可达性缺口——gateway keeper 与浏览器端无代理支持，DNS 污染环境下主网不可用。
- **最大 1 个风险**：operator 热钥管理真实划扣权（onlyOperator、无 pause/无时间锁、合约未审计），叠加 botchain.ai 域不可达的运维面——热钥失窃或链路中断都直接打击真实资金结算。
