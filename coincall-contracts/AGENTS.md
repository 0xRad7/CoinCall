# AGENTS.md — 给后续 Agent 的操作说明

> PayVault 合约仓（coincall-contracts，P0-2）。改代码前先读 `CONSTRAINTS.md`（铁律与偏差记录）。

## 全量测试入口

```bash
uv sync                                                  # 环境自建（solc 首用自动下载 0.8.24）
uv run pytest                                            # 全量单测（eth-tester 本地 EVM，无网络，默认排除 needs_funds）
uv run pytest --cov -q                                   # 带覆盖率（门禁 ≥80%）
uv run pytest tests/test_eip712_golden.py -q             # 只跑黄金向量（T8）
uv run pytest -m needs_funds                             # 真实链上写用例（目前由部署脚本承担，无此类 pytest 用例）
uv run ruff check . && uv run ruff format --check . && uv run mypy   # 静态三连（每轮强制）
```

- 测试布局：`tests/test_payvault_behavior.py`（T4~T7 + 补充分支）、`tests/test_eip712_golden.py`（T8 黄金向量三层断言）、`tests/test_compile_artifacts.py`（产物防漂移）、`tests/test_chain_guards.py`（链上守卫）。
- 需要重编译合约：`uv run python -c "from payvault.compile import compile_all; compile_all()"`，产物自动写 `artifacts/`（`test_artifacts_fresh` 会拦截漂移，改完源码必须重编译再提交）。
- 真实部署/冒烟（消耗测试网 BOT）：`uv run python script/deploy_testnet.py`（= `--network testnet`），结果覆盖写 `deployments/testnet-968.json`。主网模式 `--network mainnet` 默认只 dry-run（签名前 guard 退出）；真部署需 `--yes-i-will-deploy` 且**只能由发起人手动执行**（mainnet-readiness.md §0 红线：AI 助手不得自主发起任何主网链上写操作），产物 `deployments/mainnet-677.json`。

## 绑定校验说明（其他仓如何对齐本仓）

本仓对外暴露三个"接线锚点"，其他仓（网关/keeper/SDK）按下述顺序校验：

1. **部署事实**：`deployments/testnet-968.json` 是唯一事实来源——顶层 `payVault`/`token`/`tokenSymbol`（现行纪元=真 USDT）、`chainId=968`、`operator`、`domainSeparator`、`structType`、ABI 文件路径；`epochs` 数组是退役纪元账本（epoch 1=MockUSDT，reason token switch）。禁止在其他仓硬编码这些值以外的来源。
2. **EIP-712 字节级口径**（网关验签汇合阶段必做）：用你仓自己的 digest 实现，对 `vectors/eip712_golden.json` 的每个 case 重建 digest，断言与 `case["digest"]` 逐字节一致，且 `ecrecover(digest, v, r, s) == case["address"]`。分量不一致时比对 `case["domainSeparator"]`（域编码问题）或 `case["structHash"]`（struct 编码问题）定位。**向量文件不得手改。**
3. **链上绑定复核**（可选，只读）：`eth_call` PayVault 的 `DOMAIN_SEPARATOR()` 应等于你在 chainId=968 + `payVault` 地址下本地计算的值（`deployments.testnet-968.json.domainSeparator` 已记录，可直接比对）。

注意实现陷阱（本仓已踩过，见 CONSTRAINTS.md C-01）：web3 7.16 的 `Web3.solidity_keccak` 对 address 字符串值产出错误哈希——哈希请走 `eth_abi.encode + keccak`。Nonce 是 **bytes32**（EIP-3009 正典，0x 前缀 32 字节 hex；曾误用 uint256 已整改，见 E-1/C-07），struct 类型名固定 `Authorization`，六字段序 `from,to,value,validAfter,validBefore,nonce`。废弃合约地址存档在 `deployments/superseded/`，接线只认 `deployments/testnet-968.json` 现行地址。

## 私钥纪律

私钥只从环境变量读，任何日志/输出不得出现私钥，只允许地址与交易哈希：
- 测试网：`BOT_CHAIN_TEST_PRIVATE_KEY`（缺省解析 `coincall-bot-chain-api/.env`）。
- 主网：仅 `DEPLOYER_PRIVATE_KEY`（`0x` hex 或 0600 权限私钥文件路径；权限不符直接拒绝）——
  绝不回退测试 .env、绝不用 anvil 助记词路径。

## 提交纪律

Conventional Commits 且必须带 scope：`type(scope): 描述`（本机全局 commit-msg 钩子机械校验）。test 与 impl 分开提交；收尾 tag `d0-contracts`。
