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
script/             deploy_testnet.py 测试网部署+冒烟 / generate_golden_vector.py
deployments/        testnet-968.json 部署事实
payvault/           Python 工具链（编译/EIP-712/链上提交）
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
uv run python script/deploy_testnet.py
```

## 合约 API 一览

| 函数 | 语义 |
|---|---|
| `chargeWithSigBatch((address provider, Authorization auth, uint8 v, bytes32 r, bytes32 s)[] calls) external onlyOperator` | 逐笔：时间窗→nonce 未用→ecrecover==from→`token.transferFrom(from,this,value)`→`credits[provider]+=value`；单笔失败跳过+`ChargeFailed`，不回滚整批；`MAX_BATCH=50` |
| `providerWithdraw(address to, uint256 amount) external` | 只提 msg.sender 自己的 credits；**无 pause/无 owner 锁/无时间锁**（铁律 P8） |
| `operatorUpdate(address newOperator) external onlyOperator` | operator 轮换（单步） |
| view | `operator() / token() / DOMAIN_SEPARATOR() / totalCredits() / credits(address) / usedNonces(uint256) / MAX_BATCH()` |

事件：`Charged(provider, from, value, nonce)` / `ChargeFailed(provider, from, reason)` /
`Withdrawn(provider, to, amount)` / `OperatorUpdated(oldOp, newOp)`。

EIP-712：domain `{name:"PayVault", version:"1", chainId, verifyingContract}`，
struct `Authorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,uint256 nonce)`
（`to` 必须等于合约地址；字节级口径见 `vectors/eip712_golden.json`）。

## 诚实边界

合约未经第三方审计：额度上限由消费者 `approve` 自主控制 + 提现路径永开（P8）+ 演示小额。
MockUSDT 仅限测试网与本地测试，绝不部署主网。
