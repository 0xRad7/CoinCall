# coincall-contracts — PayVault 结算合约仓

BOT Chain（测试网 chainId=968）上的 **PayVault** 合约：手工复刻 Base USDC 原生 EIP-3009/x402 语义的链上结算心脏。消费者离线签 EIP-712 `Authorization`，keeper（operator）批量 `chargeWithSigBatch` 把 USDT 从消费者钱包划入合约并按 Provider 记应收账本；Provider 随时 `providerWithdraw`。

- 规范正文：`coincall-docs/04_settlement.md` §2（合约规格与不变量 I1~I4）
- 任务验收：`coincall-docs/09_p0_p1_tasks.md` P0-2
- 部署事实（其他仓接线唯一来源）：`deployments/testnet-968.json`

## 仓库结构

```
contracts/          PayVault.sol / MockUSDT.sol（solc 0.8.24, shanghai）
artifacts/          编译产物（bytecode+ABI JSON）入库，禁止手改
vectors/            eip712_golden.json 黄金向量（digest 字节级口径锁死）
tests/              pytest：eth-tester 本地 EVM 行为测试（T4~T8 等）
script/             deploy_testnet.py 网络驱动部署+冒烟（--network testnet|mainnet）/ generate_golden_vector.py
deployments/        <network>-<chainId>.json 部署事实（testnet-968.json 双纪元；mainnet-677.json 主网）；superseded/ 废弃地址存档
payvault/           Python 工具链（编译/EIP-712/网络表/链上提交）
```

## 快速开始

```bash
uv sync                                    # 安装依赖（solc 二进制首用自动下载）
uv run python -c "from payvault.compile import compile_all; compile_all()"  # 编译 → artifacts/
uv run pytest                              # 全量单测（eth-tester，无网络）
uv run pytest --cov                        # 覆盖率（门禁 ≥80%）
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

测试网部署（需要测试网 BOT 余额，私钥从 `BOT_CHAIN_TEST_PRIVATE_KEY` 或
`coincall-bot-chain-api/.env` 读取，永不打印）：

```bash
uv run python script/deploy_testnet.py    # = --network testnet：部署 PayVault(token=测试网真 USDT) + 全量冒烟
```

## 主网部署（人工执行）

主网 = BOT Chain **chainId 677**（`https://rpc.botchain.ai/`），网络表单一事实源
`payvault/networks.py`（`--rpc/--chain-id/--token` 可覆写，RPC 仍受 bohr.life/botchain.ai
域白名单约束）。**真部署只能由发起人手动执行**（mainnet-readiness.md §0 红线）：
脚本默认只 dry-run（连接 + 签名前 chainId==677 断言 + 主网 USDT 只读预检 + 余额只读检查 +
离线构造部署 calldata），随后在**签名前** guard 退出（exit 0，未签名任何交易、未发送任何交易）；
唯 `--yes-i-will-deploy` 双确认后才真部署。

**前置**

- 署名者：env `DEPLOYER_PRIVATE_KEY`（`0x` hex，或 **0600 权限**私钥文件路径——权限不符直接拒绝；
  主网不回退测试 .env、不用 anvil 助记词）；该 EOA 需持有主网 BOT（部署 gas，经
  bridge.bohr.life / dex.botchain.ai 入金）。
- operator：PayVault 构造器第二参，默认 keystore 托管地址 `0xb1ea3EA94e2Fd7Cb9244cD460dA863FC4b61033A`
  （bot-chain-api keystore 新建账户，mainnet-readiness.md §2.1），`--operator 0x…` 可覆写。
- 网络可达性：部署机需可直连 `rpc.botchain.ai`；DNS 污染环境 `export HTTPS_PROXY=http://<代理>`
  即可（requests 信任 env，**代码不硬编码代理**；bohr.life 测试网会话始终直连忽略代理）。

**命令**

```bash
# dry-run（默认）：只读检查 + 待确认摘要，签名前 guard 退出
DEPLOYER_PRIVATE_KEY=0x… uv run python script/deploy_testnet.py --network mainnet

# 真部署（仅发起人手动执行）：部署 PayVault + 链上核验（token()/operator()/DOMAIN_SEPARATOR
# 以 677+新地址重建比对），不跑自动冒烟——anvil 垫付/批量 charge 是测试网专属
DEPLOYER_PRIVATE_KEY=0x… uv run python script/deploy_testnet.py --network mainnet --yes-i-will-deploy
```

**产物与验证**

- 产物：`deployments/mainnet-677.json`（网络分文件，**绝不覆盖 testnet-968.json**；字段对齐测试网
  产物，`eip712.domain.chainId=677`，`smoke.mode=manual`）。
- 源码验证：`https://scan.botchain.ai`（Blockscout 标准验证，solc 0.8.24 / shanghai / optimizer 200）。
- 人工最小冒烟（真金小额，发起人执行，mainnet-readiness.md §6）：未授权 charge `bad_signature`
  拒绝 → 1 笔最小额 Charged + I4 对账（`USDT.balanceOf(PayVault) == totalCredits()`）→
  复核主网 USDT 源码 blacklist 语义。
- 主网 USDT（`0xaBabc7Ddc03e501d190C676BF3d92ef0e6e87a3C`）/IdentityRegistry 均为官方既有合约
  （只换配置不部署）；MockUSDT 绝不上主网。

## 合约 API 一览

| 函数 | 语义 |
|---|---|
| `chargeWithSigBatch((address provider, Authorization auth, uint8 v, bytes32 r, bytes32 s)[] calls) external onlyOperator` | 逐笔：时间窗→nonce 未用→ecrecover==from→`token.transferFrom(from,this,value)`→`credits[provider]+=value`；单笔失败跳过+`ChargeFailed`，不回滚整批；`MAX_BATCH=50` |
| `providerWithdraw(address to, uint256 amount) external` | 只提 msg.sender 自己的 credits；**无 pause/无 owner 锁/无时间锁**（铁律 P8） |
| `operatorUpdate(address newOperator) external onlyOperator` | operator 轮换（单步） |
| view | `operator() / token() / DOMAIN_SEPARATOR() / totalCredits() / credits(address) / usedNonces(bytes32) / MAX_BATCH()` |

事件：`Charged(provider, from, value, bytes32 nonce)` / `ChargeFailed(provider, from, reason)` /
`Withdrawn(provider, to, amount)` / `OperatorUpdated(oldOp, newOp)`。

EIP-712：domain `{name:"PayVault", version:"1", chainId, verifyingContract}`，
struct `Authorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)`
（`to` 必须等于合约地址；nonce 为 bytes32——EIP-3009 正典口径；字节级口径见 `vectors/eip712_golden.json`）。

## 诚实边界

合约未经第三方审计：额度上限由消费者 `approve` 自主控制 + 提现路径永开（P8）+ 演示小额。
MockUSDT 仅限本地测试（测试网纪元已切换真 USDT，见 deployments epochs 账本），绝不部署主网。
