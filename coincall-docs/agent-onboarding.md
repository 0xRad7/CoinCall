# Agent 接入 CoinCall 平台：完整说明

> 面向对象：想让 Agent 自主使用付费服务的开发者（Agent 宿主 Operator）。
> 全程约 **5 分钟**（配置一次，之后 Agent 全自主）。以下每一步都是真实可执行的，无占位。

---

## 〇、前置条件（一次性）

| 需要 | 怎么获得 | 耗时 |
|---|---|---|
| Python 3.11+ 和 [uv](https://docs.astral.sh/uv/) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | 1 分钟 |
| 一个 BOT Chain 测试网钱包 | OKX 添加自定义链（RPC `https://rpc.bohr.life/`，链 ID 968），或 SDK 生成 | 1 分钟 |
| 钱包里有测试 USDT + 少量 BOT 付 gas | 水龙头领取（`https://faucet.bohr.life/basic`） | 1 分钟 |

**不需要**：注册账号、邮箱、密码——钱包即账户。

---

## 一、获取接入凭证（两个文件进本机）

### 1. API Key（平台的调用凭证）

方式 A：控制台（`http://127.0.0.1:5173` → 消费端工作台 → 连接钱包 → 申请 key，明文只显示一次）。

方式 B：一行命令（钱包地址换你的）：

```bash
curl -s -X POST http://127.0.0.1:8020/apikeys \
  -H "content-type: application/json" \
  -d '{"consumer_wallet": "0x你的钱包地址"}'
# 返回 {"api_key": "cck_xxxx...", ...} —— 明文只此一次
```

### 2. 钱包私钥文件（0600 权限）

```bash
# 用 SDK 生成专用付费钱包（推荐：与主钱包隔离，只放小额）
uv run --from coincall-sdk python -c "
from coincall.wallet import LocalWallet
w = LocalWallet.create()
print('地址:', w.address)
" # 记下地址，把私钥存入文件：

echo "0x你的私钥" > ~/.coincall/wallet.key
chmod 600 ~/.coincall/wallet.key
```

### 3. 一次性链上准备（唯一需要钱包签名的地方）

授权 PayVault 可以从你的钱包扣款（**这一步是链上交易，需要 gas**；之后 Agent 的每次付费都不再需要链上操作）：

```bash
uv run --from coincall-sdk python -c "
from coincall.wallet import LocalWallet
w = LocalWallet.from_key(open('$HOME/.coincall/wallet.key').read().strip())
print(w.approve_vault('10'))   # 授权 10 USDT 额度给 PayVault（后续可随时调整/撤销）
"
```

然后从你的主钱包向这个专用地址转一点测试 USDT（比如 5 个）。

---

## 二、接入方式（三选一，按你的 Agent 宿主选）

### 方式 A：MCP Server（推荐——任何 MCP 宿主：Claude Desktop / ZCode / Cursor / 自研）

**安装**：无需安装——`uv` 会按需拉取（零克隆零 pip）。

**配置**：在你的 MCP 客户端配置中加一段（以 Claude Desktop `claude_desktop_config.json` 为例）：

```json
{
  "mcpServers": {
    "coincall": {
      "command": "uv",
      "args": ["run", "--from", "coincall-sdk", "coincall-mcp"],
      "env": {
        "COINCALL_API_KEY": "cck_你的key（可省——省略时自动读 ~/.coincall/apikey 0600 缓存）",
        "COINCALL_WALLET_KEY": "/Users/你/.coincall/wallet.key",
        "COINCALL_GATEWAY_URL": "http://127.0.0.1:8030",
        "COINCALL_CORE_URL": "http://127.0.0.1:8020",
        "COINCALL_TOTAL_BUDGET_RAW": "1000000",
        "COINCALL_DAILY_BUDGET_RAW": "200000",
        "COINCALL_PER_CALL_BUDGET_RAW": "50000",
        "COINCALL_ALLOWED_SERVICES": "binance_future_ai_increase_top_n",
        "COINCALL_MIN_INTERVAL_S": "2"
      }
    }
  }
}
```

> 后五个 `BUDGET/ALLOWED` 变量是 **L0 安全护栏**（本地策略引擎）：总额/日额/单笔上限 + 服务白名单 + 最小调用间隔。私钥用**文件路径**（SDK 自己读 0600 文件），明文不进配置文件。
> `COINCALL_API_KEY` 同理可省：机器上跑过一次接入引导（`ensure_api_key` / consumer_agent_v2）后，`~/.coincall/apikey`（0600）已缓存明文，MCP 自动回读——**宿主 json 里可以完全不放明文 key**。

配好后重启宿主，Agent 的工具列表会出现 CoinCall 的 5 个工具（见下节）。

### 方式 B：Python SDK（自研 Agent / LangChain 等）

```bash
uv add coincall-sdk   # 或 pip install coincall-sdk
```

```python
from coincall import Client
from coincall.policy import PolicyConfig
from coincall.wallet import LocalWallet

client = Client(
    api_key="cck_你的key",
    wallet=LocalWallet.from_key("~/.coincall/wallet.key"),
    policy=PolicyConfig(
        total_budget_raw=1_000_000,     # 总额 1 USDT（10000 raw = 0.01 USDT）
        daily_budget_raw=200_000,
        max_per_call_raw=50_000,
        allowed_service_ids=["binance_future_ai_increase_top_n"],
    ),
)

result = client.call("binance_future_ai_increase_top_n", {"query": "BTC 走势"})
print(result.body, result.receipt_id, result.charged_raw)
```

### 方式 C：Skill + CLI（ZCode / Claude 等有 skill 机制的宿主）

```bash
uvx --from coincall-sdk python skills/coincall-consumer/scripts/call.py status
```

---

## 三、Agent 拿到的工具面（5 个）

| 工具 | 干什么 | 花钱吗 |
|---|---|---|
| `wallet_status` | 自查：地址/余额/授权/L0 预算余量（挂载后第一件事） | 免费 |
| `catalog` | 列出平台在售服务+定价+schema | 免费 |
| `service_quote` | 单服务报价 + **内嵌 advice 建议**（更优推荐+理由+安全提示） | 免费 |
| `paid_service_call` | **付费调用**（本地签名 → 网关 → 结果+收据） | **花钱（唯一的）** |
| `spend_report` | 本地账本：已花/剩余/最近笔明细（intent→receipt→onchain） | 免费 |

**Agent 的工作纪律**（SKILL.md 已内置，MCP 工具描述里也写了）：

```
1. 挂载后先 wallet_status 自查（缺钱/缺授权 → 停下报告用户，不要硬试）
2. catalog 找服务 → service_quote 看价（顺带拿到 advice 建议）
3. advice 说 verb=switch → 改调 recommend 并向用户说明理由
4. paid_service_call 失败 → 把人话错误转告用户，绝不自动重试付费调用
5. 每笔付费后一句话汇报：买了什么/花了多少/收据号
```

---

## 四、安全模型（为什么敢把钥匙给 Agent）

| 层 | 机制 | 状态 |
|---|---|---|
| 结构层 | 金额只能=目录定价（SDK/网关/合约三处校验）；授权对象锁死 PayVault；失败从未扣款 | ✅ 已交付 |
| 客户端 L0 | 总额/日额/单笔/白名单/速率（本地策略引擎，预算落盘重启不清零） | ✅ 已交付 |
| 服务端咽喉 | 按钱包日累计上限（绕过 SDK 直打网关也绕不过，默认 50 USDT/日） | ✅ 已交付 |
| 爆炸半径 | 专用小额钱包（只放你要花的量）；钥匙被偷最坏=烧掉日限额买服务，偷不走余额 | ✅ 设计如此 |
| 升级线 | 签名代理进程（SDK 被投毒也摸不到钥）/ 合约级会话密钥 | 📋 路线图 |

**诚实边界**：Agent 进程被完全控制且私钥文件被读取时，损失上限=钱包余额内的日限额（所以我们建议专用钱包只放小额）。这是"本地持钥"范式的物理上限，与 Coinbase AgentKit"无限制额无白名单"相比已是最强客户端防线。

---

## 五、一次完整调用长什么样（时序）

```
Agent: wallet_status → 余额 4.9 USDT / 授权 10 / 日额余 0.19
Agent: catalog → [{binance_future_ai_increase_top_n, 0.01 USDT/次}]
Agent: service_quote(binance_future_ai_increase_top_n) → 价格 0.01 + advice:{verb:"keep"} + 预算够
Agent: paid_service_call(binance_future_ai_increase_top_n, {query:"BTC 走势"})
  └─ SDK 本地: L0 检查 ✓ → EIP-712 签名（钥不出进程）→ 网关验签→转发 Provider
  ← 结果 JSON + X-Receipt-Id + X-Charged-Raw=10000（此刻钱还没动）
Agent: 汇报 "调用 RadAI 成功，花费 0.01 USDT，收据 rcp_xxx"
（约 30 秒后 keeper 批量上链，链上 Charged 事件可在 scan.bohr.life 核验）
Agent: spend_report → 今日已花 0.01 / 剩 0.19 / 收据列表
```

---

## 六、故障排查

| 症状 | 原因与解法 |
|---|---|
| `402 insufficient_balance` | 钱包 USDT 不够 → 水龙头领取/转入 |
| `402 insufficient_allowance` | PayVault 授权耗尽 → 重跑第一·3 步 approve |
| `402 wallet_daily_cap_exceeded` | 触达平台侧日限额（默认 50 USDT/日）→ 明日自动重置或联系平台调额 |
| `PolicyViolationError 总额预算…` | L0 本地预算触底 → 调高 env 或清理 `~/.coincall/spend_state.json` |
| MCP 连不上网关 | 检查三服务是否在跑（`up.sh status`）+ macOS 代理（`NO_PROXY=*`） |
