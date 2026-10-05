# CONSTRAINTS.md — coincall-sdk 研发规范（精简移植自 coincall-gateway）

> 本文件是 coincall-sdk 的规范来源；与 `pyproject.toml`（lint/类型/测试唯一配置源）配套。
> 违反规范的事实必须沉淀到 §D；规范演进以追加条款方式修改，不允许静默删改。

## §A 铁律（违反 = 返工）

| # | 铁律 | 落地点 |
|---|---|---|
| A1 | **私钥不出消费者机器**：私钥只进本地存储（env/0600 文件）与内存；任何网络载荷（HTTP 头/JSON body/链上 tx）不得携带私钥；链上操作只发**本地签名的 raw tx** | `coincall/wallet.py` |
| A2 | **零网络单测**：unit 测试禁止真实网络；HTTP 一律 httpx MockTransport、链读写经注入的 FakeChain | `tests/` |
| A3 | **签名独立实现**：EIP-712 digest 在本仓手工编码（不 import 网关/合约仓代码），经 T17 黄金向量（`../coincall-contracts/vectors/eip712_golden.json`）逐字节锁死 | `coincall/signing.py` |
| A4 | **chainId==968 断言先于签名**：本地直签任何 tx 前断言链 ID（防错链签名）；gas 恒 20 gwei（POA） | `coincall/chain.py` |
| A5 | **预算本地执行**：budget_raw 超限即拒调（03 §6：平台不替 consumer 管预算） | `coincall/client.py` |
| A6 | **402 必须转人话**：质询体的 payment 块（approve_to/定价/余额）转成可执行的指引文案，不透传裸 JSON | `coincall/client.py` |
| A7 | live/needs_funds 冒烟只用公开 anvil 测试账户（无价值测试网），测试代码可持有其公开助记词派生 key | `tests/test_live_smoke.py` |

## §B 工程规范

1. **工具链**：uv；`pyproject.toml` 唯一配置源；dev 版本组逐字对齐 coincall-gateway
   （pytest==9.1.1 / pytest-asyncio==1.4.0 / pytest-cov==7.1.0 / ruff==0.16.10 / mypy==2.4.0）。
2. **静态三连**（每批写完立即执行，全过才提交；`.githooks/pre-commit` 机械强制）：

   ```bash
   uv run ruff check . && uv run ruff format --check . && uv run mypy coincall tools
   ```

3. **类型**：`coincall/` 与 `tools/` 全量注解；`# type: ignore` 必须带理由。
4. **测试**：测试先行、分型提交（test/impl 分开）；质量门 `uv run pytest -q -m unit --cov=coincall --cov-fail-under=80`。
5. **提交纪律**：Conventional Commits 带必填 scope：`type(scope): 描述`。

## §C 静态检查门（每轮代码编写强制）

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy coincall tools
uv run pytest -q -m unit --cov=coincall --cov-fail-under=80
```

## §D 犯错沉淀区（violation log）

| 编号 | 日期 | 违反事实 | 根因 | 新增/强化约束 |
|---|---|---|---|---|
| （继承）C-07 | 2026-10-06 | gateway 实跑：httpx 默认 `trust_env=True` 吞 macOS 系统代理，localhost 请求被拦成 502 空体 | httpx 默认读系统代理，localhost 不在 NO_PROXY | 本仓所有 httpx 客户端一律显式 `trust_env=False`；web3 的 requests Session 显式 `session.trust_env=False`（同源问题） |
| （继承）C-08 | 2026-10-05 | 改 pyproject 后 ruff 缓存全量失效，历史潜在问题现形 | ruff 按配置哈希缓存 | pyproject/ruff 配置变更后的第一次门禁必须 `ruff check --no-cache`（本仓建仓首跑即用） |

## §E 偏差清单

- SDK 不校验收据 HMAC 签名（`X-Receipt-Sig` 的 secret 在网关侧；SDK 只透传收据头——03 §5 的"校验收据签名"降级为透传，P2 可加可选 secret 校验）。
- `bind-wallet` / `GET /consumer/me/balance` 等 core 消费面端点未在 core 落地（P0 子集只有 /apikeys 签发+validate）；SDK 以 POST /apikeys 的 consumer_wallet 完成钱包绑定，不做独立 bind-wallet 调用。
- 钱包文件仅支持 0600 权限裸 hex 文件；keyring 集成属 P2。
- 链读写直接打 rpc.bohr.life（不经 coincall-bot-chain-api 代理）——SDK 自管钱包必须本地直签，bot-chain-api 代管路径仅文档提示。
