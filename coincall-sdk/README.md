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

**主网切换（可选）**：`export COINCALL_NETWORK=mainnet` 即切 BOT Chain 主网
**677**（rpc.botchain.ai，USDT `0xaBabc7…87a3C`）；缺省 `testnet`（968，行为与历史版本一致）。
字段级精确覆写：`COINCALL_CHAIN_ID` / `COINCALL_RPC_URL` / `COINCALL_TOKEN_ADDRESS`
（显式构造参数 > env > 网络表缺省）；主网 PayVault 地址不在表内，需
`LocalWallet(pay_vault="0x主网金库地址")` 显式传入。

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

### 5) Agent 框架（MCP）挂载即接入——五工具面

```bash
uv run python tools/mcp_server.py        # stdio MCP server，五个工具：
# wallet_status      —— 钱包健康自查（地址/链/USDT 余额/对 PayVault 授权/L0 限额现值；
#                       余额或授权为 0 时返回 hint_* 人话：水龙头 mint / approve_vault）
# catalog            —— 列可用付费服务（ID/定价/input schema）
# service_quote      —— 单服务报价：定价/收款方 PayVault/自己的余额与授权/L0 预算余量
# paid_service_call  —— 付费调用（唯一花钱工具；EIP-712 支付授权 + X-PAYMENT；
#                       描述里写死纪律：先 catalog+quote，失败不要自动重试）
# spend_report       —— 本地账本聚合：已花/剩余/最近 N 笔（intent→receipt→onchain）
```

Claude Code / 任何标准 MCP client 指到该命令即可。工具面 = Agent 决策闭环
（自查 → 看目录 → 看价 → 付费 → 汇报）；预算/白名单由 `COINCALL_*` env 注入
L0 引擎在本地强制（见下节）。macOS 代理环境加 `NO_PROXY=*` 防系统代理劫持
localhost（C-07）。

### 6) Skill（coincall-consumer）——给 Agent 的使用纪律

仓内自带 `skills/coincall-consumer/`（SKILL.md + CLI 薄壳 + 安全清单），教 Agent
**如何安全地**用上面的五工具/SDK：六步流程（自查→目录→报价→超限问人→调用→汇报）、
三条铁律（付费失败不自动重试 / 预算触底即停 / 每笔报收据）、密钥纪律。

```bash
# ZCode：把 skills/ 目录同步到 ~/.agents/skills/（或项目级 .agents/skills/）
cp -r skills/coincall-consumer ~/.agents/skills/

# CLI 直用（内部走 SDK，天然带 L0；stdout=结果 JSON，stderr=人话错误）：
python skills/coincall-consumer/scripts/call.py status            # 自查
python skills/coincall-consumer/scripts/call.py quote svc_rad_ai  # 报价
python skills/coincall-consumer/scripts/call.py call svc_rad_ai '{"query":"BTC"}'  # 付费（真实扣款）
python skills/coincall-consumer/scripts/call.py report            # 账单
```

安全边界五问五答（最多花多少/会不会转陌生地址/金额会不会被改/花哪了能看吗/能立刻停吗）
见 `skills/coincall-consumer/references/security.md`。

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

- **缺省测试网**：链 968（rpc.bohr.life）、MockUSDT（公开 mint）、PayVault 演示合约。
  主网 677 经 `COINCALL_NETWORK=mainnet` 切换（见《主网切换》）；`mint()` 仅测试网存在。
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


## Agent 资金安全（L0 策略引擎）

Agent 持钥的五条资金风险中，SDK 内置本地策略引擎拦截失控循环/金额滥用/目标限面/不可追溯（完整威胁模型见 coincall-docs/design/agent-wallet-trust.md）：

```python
from coincall.policy import PolicyConfig

c = Client(
    api_key="cck_…",
    wallet=wallet,
    policy=PolicyConfig(
        total_budget_raw=1_000_000,  # 总额硬顶（0.01 USDT=10000 raw）
        daily_budget_raw=200_000,  # 日额（UTC 日界自动重置）
        max_per_call_raw=50_000,  # 单笔上限
        allowed_service_ids=["svc_rad_ai"],  # 服务白名单（默认拒绝）
        min_interval_s=2.0,  # 最小调用间隔
        max_calls_per_hour=60,  # 小时速率
    ),
)
```

- **预算状态落盘** `~/.coincall/spend_state.json`（0600 原子写）——重启不清零；
- **审计账本** `~/.coincall/ledger.jsonl` 只追加三段（intent→receipt→onchain），与平台流水、链上 Charged 三方可对账；
- **触底自动只读**（catalog 可看、付费拒绝）；`policy_state_dir` 参数可隔离多实例；
- MCP 环境变量：`COINCALL_{TOTAL,DAILY,PER_CALL}_BUDGET_RAW`、`COINCALL_ALLOWED_SERVICES`、`COINCALL_MIN_INTERVAL_S`、`COINCALL_MAX_CALLS_PER_HOUR`（旧 `COINCALL_BUDGET_RAW` 兼容映射总额）；
- 诚实边界：密钥被进程级窃取属本地持钥范式上限——**专用小额钱包**控制爆炸半径（像零钱包一样对待它）。
