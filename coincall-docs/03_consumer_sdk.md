# 03 — 消费面：api key、本地付费钱包与 SDK/skill 封装（v2 修订）

> v2 变更：**充值/余额模型整体替换**——v1 的"转账进平台收款地址+确认轮询"作废（铁律 P7 资金不过平台手）。消费者改为：SDK 本地生成专用付费钱包（私钥永不离开消费者机器）+ 自主 USDT approve(PayVault) + 每次调用本地 EIP-712 签名支付授权。

> 前置阅读：00 总览（铁律 P4：消费低门槛）。自包含：本文档足以独立实现本平面。

## 1. 职责边界

**做**：api key 生命周期、本地付费钱包生成与绑定、approve 指引、消费账单、Python SDK 与 Agent 框架的 skill（工具）封装。
**不做**：计费与结算执行（02 只做影子闸门+队列，扣款在 04 keeper/合约）；除支付授权外的链上签名（经 coincall-bot-chain-api；支付授权 EIP-712 签名在消费者本地进程内完成）。

## 2. api key（凭证模型）

```
POST /consumer/keys            签发：{consumer_id?, display_name?} →
  {key_id: "key_9f3a…", secret: "sk_live_<32B>", created_at}
  ⚠ secret 仅此一次返回（与 coincall-bot-chain-api 的 reveal 语义一致）
GET  /consumer/keys            列表（不回显 secret）
DELETE /consumer/keys/{key_id} 吊销（立即生效，网关侧查吊销表）
```

- key 与 consumer 账户 1:N；consumer 账户在首次签发时自动创建（**无需注册、无 8004 要求**——铁律 P4）；
- **可选升级**：`PUT /consumer/me/bind-identity {agent_id, owner}`——把 8004 身份挂到账户上（仅用于展示/信誉归集，不影响调用权限）；
- 存储：key_id + secret 的 argon2/sha256(盐) 哈希，绝不明文落盘（沿用 coincall-bot-chain-api 的脱敏纪律）。

## 3. 本地付费钱包与资金准备（v2：替代"充值"）

**核心变化**：没有平台收款地址、没有充值确认轮询。消费者资金准备三步，全部自主：

```
① SDK 初始化：本地生成专用付费钱包（eth_account Account.create()）
   → 私钥仅存消费者本地（keyring/文件/env），平台与合约都拿不到
   → 输出钱包地址，与 api key 绑定：PUT /consumer/me/bind-wallet {address}
② 转入 USDT：消费者从任意账户向该钱包转 USDT
   （体验者可经 coincall-bot-chain-api POST /tokens/erc20/transfer 自助完成）
③ 授权额度：该钱包 USDT.approve(PayVault, 限额)
   （SDK 提供 approve 引导：调 coincall-bot-chain-api approve 端点，from=消费者钱包——
    若消费者钱包由 coincall-bot-chain-api 代管则可直接代办；自管钱包则 SDK 本地签）
```

钱包余额/可用额度查询：`GET /consumer/me/balance` →
`{wallet, usdt_balance_raw, vault_allowance_raw, available_raw(=min(两者)−在途), currency}`（链上读经 coincall-bot-chain-api）。

**每笔调用的签名职责**（发生在消费者进程内，见 00 §3 时序①）：
SDK 组装 Authorization{from,to,value,validAfter,validBefore,nonce} → 本地私钥 EIP-712 签名 → X-PAYMENT 头。签名即支付承诺——这是 x402 模拟能成立的原点。

> v1 的"tx_hash 申报充值/60s 轮询确认/防重放校验"整节作废。

~~① 向 deposit_address 转账 → ② POST /consumer/topup 申报 → ③ 60s 轮询确认 → ④ ledger 入账 + topups 流水~~——被上文「本地钱包三步」结构性替代，保留此行仅作变更痕迹。

## 4. 余额与账单

```
GET /consumer/me/balance        → {balance_raw, balance, currency:"USDT"}
GET /consumer/me/calls?limit=50 → 消费流水（join calls 表：服务、金额、状态、receipt_id）
GET /consumer/me/receipts/{id}  → 单张收据 + HMAC 验签说明
```

## 5. Python SDK（`<项目名>-sdk`，赛时最小包）

```python
from fortyswo import Client            # 名随项目定

# 初始化（首次自动生成/装载本地付费钱包）
c = Client(api_key="sk_live_…", signer=local_wallet, base_url="http://…:8020")
result = c.call("svc_translate_v1", {"text": "hello", "source": "en", "target": "zh"})
# 内部：① 本地 EIP-712 签名支付授权 → X-PAYMENT 头
#      ② 捕获 402 → 抛 InsufficientBalanceError（含 approve/转入指引）
#      ③ 自动幂等键（参数 hash） ④ 校验收据签名 ⑤ 本地预算计数器扣减
c.approve_vault("5")   # 引导：向 PayVault 授权 5 USDT 额度
c.balance()           # 钱包余额/可用额度（含在途占用）
c.catalog()           # 机读目录（ETag 缓存）
```

实现约束：仅依赖 httpx；≤200 行；同步阻塞式（Agent 进程内使用简单优先）。

## 6. skill（Agent 框架工具封装）——接入成功率的生死点

提供一个**通用工具定义**，任何 Agent 框架（function calling / MCP）挂载即接入：

```json
{
  "name": "paid_service_call",
  "description": "调用平台目录中的付费 Agent 服务并返回结果。先 catalog() 查可用服务。",
  "input_schema": {
    "type": "object",
    "properties": {
      "service_id": {"type": "string"},
      "params": {"type": "object", "description": "该服务 input_schema 定义的请求体"}
    },
    "required": ["service_id", "params"]
  }
}
```

- 工具实现 = SDK 的 `catalog()` + `call()` 两函数包装；
- 交付物：`tools/mcp_server.py`（stdio MCP server，暴露 `catalog` 与 `paid_service_call` 两个 tool）+ 一份《5 分钟接入》README（三个环境变量：API_KEY/BASE_URL/预算上限）；
- **预算上限**由 SDK 侧执行（本地计数器，超出即拒绝调用并提示运营者调整预算）——平台不替 consumer 管预算（无裁判原则的延伸）。

## 7. 验收标准（DoD）

1. unit：key 签发/吊销立即生效；bind-wallet 的地址合法性校验；SDK 的 EIP-712 签名与合约侧 digest 构造一致性（golden vector）；
2. 集成（needs_funds）：本地钱包生成 → 转入 USDT → approve(PayVault) → 可用额度可查（经 coincall-bot-chain-api 链上读）；
3. SDK：对接 mock 网关的三条路径（success / 402 payment_required / aborted-从未扣款）测试全绿；MCP server 可被任一标准 client list_tools 并成功调用。

## 8. 依赖与被依赖

- **依赖**：coincall-bot-chain-api 既有端点（erc20 approve/transfer、accounts/balances、contracts/call 读 PayVault）——**无需 05 新端点**；
- **被依赖**：02（读凭证表）、06（账单视图共用 calls；v2 无 topups 流水）。
