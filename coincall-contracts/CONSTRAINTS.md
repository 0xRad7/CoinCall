# CONSTRAINTS.md — coincall-contracts 研发规范与约束（活文档）

> 本仓是 PayVault 结算合约仓（P0-2）。本文档是本仓**唯一规范来源**，移植自
> coincall-bot-chain-api/CONSTRAINTS.md 并按合约仓形态精简。
> 任何违反规范的事实沉淀到 §D；规范演进只允许追加，不允许静默删改。
> 关联：`pyproject.toml`（lint/类型/测试配置唯一来源）、`deployments/testnet-968.json`（部署事实唯一来源）。

---

## §A 铁律（违反任何一条 = 返工）

| # | 铁律 | 落地点 |
|---|---|---|
| P1 | 链上提交只允许 `*.bohr.life` 域（`connect_testnet` 域白名单 + `trust_env=False` 会话直连，忽略环境代理）；签名前**必须**断言 `chainId == 968` | `payvault/chain.py` |
| P2 | gas 恒定 20 gwei、legacy(type-0)；链为 POA（extraData 277B），任何 web3 实例必须注入 `ExtraDataToPOAMiddleware` | `payvault/chain.py` |
| P3 | 私钥只从环境变量 `BOT_CHAIN_TEST_PRIVATE_KEY` 读取（缺省解析 coincall-bot-chain-api/.env）；**永不打印私钥**，日志只允许出现地址与交易哈希 | `script/deploy_testnet.py` |
| P4 | 提现路径永开（铁律 P8）：合约不得引入任何 pause/owner 锁/时间锁；operator 与资金安全无关（I1），合约唯一转出 = `providerWithdraw` | `contracts/PayVault.sol` + `test_p8_abi_surface_has_no_admin_locks` |
| P5 | 安全不变量 I1~I4 必须有对应测试；nonce 只在扣款成功后烧毁（keeper 可携原签名重试坏账笔） | `tests/test_payvault_behavior.py` |
| P6 | EIP-712 字节级口径以 `vectors/eip712_golden.json` 为准；digest 构造参考实现改动必须重过向量测试；向量文件不得手改（只允许 `script/generate_golden_vector.py` 重生成） | `tests/test_eip712_golden.py` |
| P7 | `artifacts/` 编译产物与 `deployments/` 部署事实必须入库，是其他仓接线唯一来源；产物漂移由 `test_artifacts_fresh` 机械拦截 | `payvault/compile.py` + `tests/test_compile_artifacts.py` |
| P8 | solc 钉死 0.8.24 / shanghai / optimizer 200 runs；升级版本 = 重编译 + 重部署 + 更新部署事实 + 记偏差 | `payvault/compile.py` |

## §B 工程规范

1. **工具链**：uv；`pyproject.toml` 是唯一配置源（ruff + mypy + pytest + coverage 全收口，标准对齐 coincall-bot-chain-api）。
2. **静态检查**：见 §C，每轮强制。
3. **类型**：公开函数全量注解；`# type: ignore` 必须带理由；禁止裸 `except:`。
4. **测试**：unit（eth-tester 本地 EVM）禁止真实网络；`needs_funds` 真实链上写用例默认排除（`addopts = -m 'not needs_funds'`），实跑走 `script/deploy_testnet.py`；覆盖率门 ≥80%。
5. **Solidity**：零依赖单文件（PayVault 目标 ≤160 行）；自定义错误优先于 require 字符串；事件 reason 用稳定短码（not_in_window/nonce_used/bad_signature/auth_to_mismatch/transfer_failed）供 keeper 机读。
6. **提交纪律**：Conventional Commits 且**必须带 scope**——`type(scope): 描述`（本机存在全局 commit-msg 钩子机械校验）；实现与测试分开提交，一次提交一个意图；收尾 tag `d0-contracts`。

## §C 静态检查门（每轮代码编写强制）

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest --cov -q          # 覆盖率 ≥80%
```

- 每写完一批代码立即执行，**全过才允许提交**；
- 全量测试入口见 `AGENTS.md`。

## §D 犯错沉淀区（violation log）

格式：`C-NN | 日期 | 违反事实 | 根因 | 新增/强化约束`

| 编号 | 日期 | 违反事实 | 根因 | 新增/强化约束 |
|---|---|---|---|---|
| C-01 | 2026-10-05 | `Web3.solidity_keccak` 对 `address` 字符串值产出**错误 digest**（uint256/bytes32 正常），险些把错误实现写进参考实现 | web3 7.16 的 `normalize_values` 在 address 字符串路径上编码错误 | EIP-712 哈希一律 `eth_abi.encode + Web3.keccak`（与 Solidity `abi.encode` 字面对应）；用 EIP-712 规范官方示例（Ether Mail，domainSeparator `f2cee375…`）仲裁；签名路径再叠一层 eth_account `message_hash` 一致性断言 |
| C-02 | 2026-10-05 | 编译误取 PATH 上 anaconda 的 solc-select 空壳（无版本设定即崩） | py-solc-x 优先 PATH 上的 `solc`，不保证用 `~/.solcx` 安装件 | `compile.solc_binary_path()` 显式解析 `~/.solcx/solc-v0.8.24` 并传 `solc_binary=`；禁止依赖 PATH |
| C-03 | 2026-10-05 | eth-account 0.13 多处 API 与旧记忆不符：`SignableMessage` 无 `.hash()`、签名结果字段为 `message_hash` 且 r/s 是 **int**、`from_mnemonic` 需先 `enable_unaudited_hdwallet_features()`；eth_keys `Signature(vrs=…)` 要求 v∈{0,1} | 版本演进 | r/s 统一 `.to_bytes(32,"big")`；v 双口径兼容（27/28 ↔ 0/1）在 `recover_signer` 归一 |
| C-04 | 2026-10-05 | `ContractFunction.encode_abi()` 不存在（web3 7.16） | API 改名 | calldata 组装统一走 `build_transaction(...)["data"]` 公开路径（`chain.calldata`），禁止触私有 `_encode_transaction_data` |
| C-05 | 2026-10-05 | revert 断言失败：eth-tester 对自定义错误抛 `TransactionFailed`，args 可能是裸选择子 bytes，也可能是 `"execution reverted: b'..'"` 字符串 | eth-tester 与 web3 异常包装不一致 | `tests/helpers.expect_revert_selector` 双路径归一（live 链 ContractLogicError.data / eth-tester TransactionFailed），选择子比对不变 |
| C-06 | 2026-10-05 | 首次部署脚本 WARN：链上 runtime 哈希 ≠ 产物 `deployedBytecodeHash` | solc 产物 runtime 在 immutable 槽位（token/operator/DOMAIN_SEPARATOR）是**占位零**，与链上 code 天然不同；MockUSDT（无 immutable）比对通过证实 | `deployments.testnet-968.json` 的 `bytecodeHash` 以 `keccak(eth_getCode)` 为权威（任何人可复算）；产物 runtime 哈希仅作编译可复现性用途 |
| C-07 | 2026-10-05 | 汇合阶段交叉验证发现互操作缺陷：本仓 nonce 用 uint256，网关冻结实现用 bytes32，typehash 不一致导致两侧签名互不相认（原 E-1 偏差） | 任务的底线清单误写 uint256，未与 04 正典（bytes32）复核即采纳 | **冻结契约的类型字段必须溯源规范正典**，任务书转述与正文冲突时以正文为准并升级提问；已整改 E-1（uint256→bytes32）+ 重生成向量 + 重部署（旧地址存档 `deployments/superseded/`），tag `d0-contracts-r1` |

## §E 偏差记录区（对 04_settlement.md / 任务书口径的偏离）

| # | 偏差 | 依据与理由 |
|---|---|---|
| E-1 | ~~`Authorization.nonce` 用 uint256~~ **已整改（2026-10-05）：uint256→bytes32，回归 04 正典**（C-07） | 汇合阶段发现与网关冻结实现 typehash 不一致、签名互不相认；现口径 `Authorization(...,bytes32 nonce)`，`usedNonces(bytes32)`、`Charged(...,bytes32 indexed nonce)` 同步，向量已重生成（主 digest `0x65a25c5e…`），旧合约 0x583aa9…c240e1E 废弃存档于 `deployments/superseded/payvault.jsonl`，现行 `0xFe91F55C…cf79C471` |
| E-2 | struct 含 `to` 字段且合约强制 `to == address(this)`（04 §2 的 struct 无 to） | 任务清单补 to 以对齐 EIP-3009 TransferWithAuthorization；强制本合约收款是 I4 记账恒等的前提，也防授权被指向任意地址 |
| E-3 | 记账键为 **provider address**（04 §2 为 providerTokenId） | 任务底线清单：`credits[provider]`、`providerWithdraw(address to, uint256 amount)`；地址键免去 ERC-8004 依赖，网关侧用 agentWallet 即 provider 地址 |
| E-4 | `operatorUpdate` 单步（04 §2 two-step 可选，未采用） | 保持 ≤160 行；operator 与资金无关（I1），最坏=扣款停摆、提现不受影响； NatSpec 已注明 |
| E-5 | 事件签名对齐任务清单：`Charged(provider,from,value,nonce)` / `ChargeFailed(provider,from,reason)` / `Withdrawn(provider,to,amount)`（04 §2 为 providerTokenId 版本） | 同 E-3；keeper/网关机读口径以 deployments JSON 内 structType 与 ABI 为准 |
| E-6 | 部署经本仓 `script/deploy_testnet.py` 直连 rpc.bohr.life，而非 04 §5 的 bot-chain-api `POST /contracts/deploy` | W2 任务边界：bot-chain-api 只读、禁止写入；提交模式（POA/20 gwei/UA/域白名单）逐字移植自其 `app/core/rpc.py`+`tx.py` |
| E-7 | ChargeFailed 的 reason 为 string 短码而非 04 §2 的 `ChargeFailed(consumer, reason)` | 任务清单签名 `(provider, from, reason)`；短码枚举见 §B.5，keeper 按短码分支重试/拉黑 |
| E-8 | 计价 token 切换：epoch 2 起为测试网真 USDT `0x75edC933…0fe3`（decimals=6，发起人决策） | 合约零改动（token 为构造器 immutable），重部署即切纪元；deployments 双纪元结构（顶层=现行，epochs=退役账本）；旧 MockUSDT 金库 `0xFe91…C471` 有第三方在途 credits（0.22 MockUSDT），**保持不动**（部署脚本快照前后一致断言） |
