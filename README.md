# coincall-sdk — CoinCall 消费者 SDK

**装包 + 三个环境变量 + 一个函数调用**，任何 Agent 就能调用 CoinCall 市场里的付费服务。
私钥永远不离开你的机器：钱包在本进程内生成/装载，每笔调用本地 EIP-712 签名支付授权
（x402 语义），资金不经平台之手。

- 导入名 `coincall`，包名 `coincall-sdk`
- 链：BOT Chain 测试网 **968**（rpc.bohr.life）；计价 token **MockUSDT**（6 位小数）
- 服务面：core（8020，目录/签 key）+ gateway（8030，付费调用）

## 5 分钟接入

### 1) 装包

```bash
cd coincall-sdk && uv sync          # 或 pip install -e .（需 httpx/web3/eth-account）
```

### 2) 三个环境变量

```bash
export COINCALL_API_KEY="cck_…"                  # core 签发（见第 3 步）
export COINCALL_WALLET_KEY="0x…"                 # 付费钱包私钥，或指向 0600 权限 key 文件
export COINCALL_BUDGET_RAW="1000000"             # 本地预算上限（最小单位；0.1 USDT 起步）
# 可选：COINCALL_GATEWAY_URL（默认 http://127.0.0.1:8030）/ COINCALL_CORE_URL（默认 http://127.0.0.1:8020）
```

### 3) 第一次拿 key + 准备资金（只需一次）

```python
import coincall
from coincall.wallet import LocalWallet

wallet = LocalWallet.from_key("0x…")  # 或 LocalWallet.create() 生成新钱包
print(wallet.address)  # ← 把这个地址交给平台方

# 测试网自助（MockUSDT 公开 mint；主网请转入真实 USDT）：
wallet.mint("10")  # 钱包进 10 USDT（本地直签 raw tx，测试网专用）
wallet.approve_vault("5")  # 向 PayVault 授权 5 USDT 额度（一次性）
print(wallet.balance())  # usdt_balance_raw / vault_allowance_raw / available_raw

import httpx  # 向 core 签发 api key（明文只回一次！）

r = httpx.post(
    "http://127.0.0.1:8020/apikeys", json={"consumer_wallet": wallet.address}, trust_env=False
)
api_key = r.json()["api_key"]  # → 填进 COINCALL_API_KEY
```

### 4) 一个函数调用

```python
from coincall import Client
from coincall.wallet import LocalWallet

c = Client(api_key="cck_…", wallet=LocalWallet.from_key(), budget_raw=1_000_000)
print(c.catalog())  # 有哪些服务、定价多少
r = c.call("svc_e2e_demo", {"text": "hello"})  # 付费调用（0.01 USDT/次）
print(r.body, r.receipt_id, r.charged_raw)  # 结果 + 收据 + 扣款额
```

余额/授权不足时不会静默失败——`coincall.errors.PaymentRequiredError` 直接告诉你下一步：

```
402 insufficient_balance: 钱包余额不足
下一步: 钱包 USDT 余额不足：本笔定价 0.01 USDT（amount_raw=10000），链上余额 raw=0，
差 10000（最小单位）。请向付费钱包转入 USDT（测试网为 MockUSDT，可 wallet.mint()）后重试。
```

### 5) Agent 框架（MCP）挂载即接入

```bash
uv run python tools/mcp_server.py        # stdio MCP server，两个工具：
# catalog            —— 列可用付费服务
# paid_service_call  —— 调用付费服务（service_id + params）
```

Claude Code / 任何标准 MCP client 指到该命令即可；预算由 `COINCALL_BUDGET_RAW` 在本地强制执行。

## 公开 API

| 入口 | 说明 |
|---|---|
| `coincall.wallet.LocalWallet.create() / from_key(src)` | 生成/导入付费钱包（src：0x 私钥、env 名、0600 key 文件） |
| `LocalWallet.approve_vault("5")` | 本地直签 approve(PayVault) raw tx（chainId==968 断言先于签名；20 gwei） |
| `LocalWallet.mint("10")` | MockUSDT 公开 mint（**仅测试网**） |
| `LocalWallet.balance()` | `usdt_balance_raw / vault_allowance_raw / available_raw` |
| `LocalWallet.sign_payment(auth)` | EIP-712 本地签名（T17 黄金向量锁死） |
| `coincall.Client(api_key, wallet, gateway_url, core_url, budget_raw)` | 消费客户端 |
| `Client.catalog()` / `Client.call(service_id, params)` | 目录（ETag 缓存）/ 付费调用（X-PAYMENT 组装 + 402 人话 + 预算闸 + 收据头透传） |
| `coincall.errors` | `PaymentRequiredError`（含 guidance）/ `BudgetExceededError` / `GatewayError` / `WalletError` |

## 测试

```bash
uv run pytest -q -m unit --cov=coincall --cov-fail-under=80   # 零网络单测（T16~T19）
uv run pytest -q -m live                                       # core 目录只读冒烟
uv run pytest -q -m needs_funds                                # 真实付费端到端 + keeper 结算
```

绑定索引：`../coincall-core/tests/TEST-20261005221052-coincall-p1.md` T16–T19。

## 诚实边界

- **这是测试网实现**：链 968（rpc.bohr.life）、MockUSDT（公开 mint）、PayVault 演示合约。
  主网部署前 `mint()` 不存在、代币地址与链 ID 都要换（构造参数已可注入）。
- **预算是本地承诺**：`budget_raw` 由 SDK 在进程内强制（03 §6），平台不替 consumer 管预算；
  换个进程/不装 SDK 即不受限——这是"无裁判"设计的代价与自由。
- **收据只透传不验签**：`X-Receipt-Sig` 的 HMAC secret 在网关侧，SDK 未做本地校验（P2 可加）。
- **幂等键 = 参数 hash**：同服务同参数的重复调用会被网关重放（不重复扣款）；
  想每次都计费请传 `call(..., idempotency_key=str(uuid4()))`。
- 私钥以明文形态存在于本机 env / 0600 文件与进程内存中——这是本地签名的必要代价；
  keyring/硬件钱包集成是 P2 事项。

## 工程纪律

见 `CONSTRAINTS.md`（私钥不出机器 / 签名独立实现 / 零网络单测 / trust_env=False /
静态三连门禁 / 分型提交）；子智能体入口见 `AGENTS.md`。
