# 02 — 数据面：402 网关运行时（系统心脏 · v2 修订）

> v2 变更：计费从"平台账本扣款"改为"X-PAYMENT 签名验签 + 影子闸门 + settle 队列"；新增 payment_scheme 多态接口。calls 表契约保持向后兼容（新增 charge 相关列见 §5）。

> 前置阅读：00 总览、01 Manifest（消费 services 契约）。自包含：本文档足以独立实现本平面。

## 1. 职责边界

**做**：统一调用入口、402 质询与计费、api key 认证（与 03 共享凭证表）、请求体 schema 校验、代理转发 Provider 端点、幂等、收据签发、调用流水落库（供 04/06）。
**不做**：服务注册与 manifest 管理（01）、消费端本地钱包绑定与 api key 签发（03）、链上结算执行（04）。

## 2. 统一调用入口（核心路由，唯一）

```
POST /call/{service_id}
Headers:
  X-Api-Key: <consumer api key>                          # 配额/限流/归集
  X-PAYMENT: base64(JSON{ from, to, value, validAfter,   # ★ 消费者进程内 EIP-712
                validBefore, nonce, v, r, s })           #   签名的支付授权（对齐 x402）
  X-Idempotency-Key: <consumer 生成，可选但强烈建议>
Body: 符合该服务 input_schema 的任意 JSON
```

三个响应出口（全部复用三段错误模型风格：`{error, detail, code, trace_id}`）：

| 场景 | HTTP | 语义 |
|---|---|---|
| 余额不足 / 无 api key 但访问了付费服务 | **402** | 付费质询（本协议的灵魂，见 §3） |
| 认证失败 / schema 不合规 / 服务不存在或 paused | 401 / 422 / 404 | 未计费，直接拒绝 |
| 计费成功且 Provider 响应 2xx | 200 | 透传 Provider 响应体 + 收据头 |

## 3. 402 质询契约（机读格式，SDK 依赖此格式）

```json
HTTP 402 Payment Required
X-Payment-Challenge-Id: chl_01H…（本次质询 ID）

{
  "error": "payment_required",
  "detail": "本服务按次计费",
  "code": "payment_missing",              // 其余码沿用 verify 判定：insufficient_balance / insufficient_allowance
  "service_id": "svc_translate_v1",
  "pricing": {"amount": "0.01", "amount_raw": "10000", "token": "USDT"},
  "wallet_balance_raw": "0",              // 链上 eth_call 实读（第③步约束检查同源）
  "payment": {                            // v2：消费者本地钱包直接签 EIP-712，资金不过平台（铁律 P7）
    "scheme": "erc3009-vault",            // SCHEME_REGISTRY 注册名（02 §7.5 冻结实现）
    "header": "X-PAYMENT",                // base64 JSON {from,to,value,validAfter,validBefore,nonce,v,r,s}
    "domain": {"name": "PayVault", "version": "1", "chainId": 968, "verifyingContract": "0x…"},
    "approve_to": "0x…PayVault",          // 授权不足时，钱包 approve 的目标合约（03 §approve_vault）
  },
  "trace_id": "…"
}
```

**计费时序（单次调用全流程）**：

```
① 认证：api key → consumer 记录（03 的凭证表）；无 key → 直接 402（带注册指引）
② 查服务：manifest（01 的缓存），status!=active → 404
③ Schema 校验：input_schema 不合规 → 422（不产生任何计费）
④ 幂等：X-Idempotency-Key 已存在且同 body → 返回上次结果（不重复计费）；
        存在但 body 不同 → 409 idempotency_conflict
⑤ 支付验证与影子闸门（替代 v1 账本扣款）：
    a) 本地验签：ecrecover(X-PAYMENT)==该 api key 绑定的消费者钱包地址；
       deadline/金额==定价/nonce 未重放（Redis 记录）
    b) 链上约束（eth_call 经 coincall-bot-chain-api，结果短缓存）：
       USDT.allowance(consumer→PayVault) ≥ 定价 且 balance ≥ 定价
    c) 影子闸门（内存/Redis，性能层非资金层，fail-closed）：
       Σ该 key 在途金额 + 定价 ≤ b) 额度 且 在途笔数 < K(默认3)
    否 → 402（§3 质询）；是 → 写 calls 流水（status=inflight）放行
⑥ 代理转发：httpx POST manifest.endpoint.url，超时 manifest.endpoint.timeout_ms
    2xx → 流水 status=success；{auth 六元组} 写入 settle 队列（keeper 消费）；
         构造收据（§4）；透传响应体
    非 2xx / 超时 → 流水 status=aborted（**从未扣款，v2 无退款逻辑**）；
         返回 {error:"service_error", code:"provider_failed"}（502）
⑦ 收据头随 200 返回：X-Receipt-Id / X-Charged-Raw
```

**关键决策：Provider 失败不收钱**（退款语义），这是"无裁判"体系下对 Consumer 的唯一保障——服务不好用的后果由 Provider 承担（复购纪律，见 00 商业边界）。

## 4. 收据（Receipt）

```json
{
  "receipt_id": "rcp_01H…",
  "call_id": "call_01H…",
  "service_id": "svc_translate_v1",
  "provider_agent_id": 137,
  "consumer_key_id": "key_9f…",
  "amount_raw": "10000",
  "status": "success",                     // success|aborted（失败=从未扣款，无 refunded）
  "result_hash": "sha256:…",               // Provider 响应体的 hash（存证）
  "created_at": "…"
}
```

- 防伪：`sig = HMAC(server_secret, canonical(receipt))`，随收据返回 `X-Receipt-Sig`；
- 落库进 `receipts` 表（06 的数据源之一）；P2 可选上链锚定（经 05 端点 B 写 8004 metadata，非演示必需）。

## 5. 调用流水（calls 表——04/06 的共享契约，字段冻结）

```sql
CREATE TABLE calls (
  call_id        VARCHAR PRIMARY KEY,
  idempotency_key VARCHAR,
  service_id     VARCHAR,
  provider_agent_id BIGINT,
  consumer_key_id VARCHAR,
  consumer_wallet VARCHAR,          -- v2：X-PAYMENT.from
  amount_raw     BIGINT,
  status         VARCHAR,   -- inflight|success|aborted|settled|bad_debt
  http_status    INTEGER,
  latency_ms     INTEGER,
  result_hash    VARCHAR,
  payment_nonce  VARCHAR,           -- v2：防重放记录（Redis 镜像）
  created_at     TIMESTAMP DEFAULT now()
);
-- settle 队列（keeper 消费，02 只写）：
CREATE TABLE settle_queue (
  call_id VARCHAR PRIMARY KEY, provider_token_id BIGINT,
  auth_json JSON, status VARCHAR DEFAULT 'pending',  -- pending|done|failed
  created_at TIMESTAMP DEFAULT now()
);
```

## 6. 非功能约束

- 幂等键保留 24h；质询不缓存；
- 单实例并发（V1 不做横向扩展，账本事务在单库内即可满足赛时规模）；
- Provider 端点调用超时上限硬顶 60s（manifest 可配更短）；
- 本平面**零链上交易**（链上读仅第③步 eth_call 约束检查；链上写只出现在 04 结算，经 keeper/ coincall-bot-chain-api）。

## 7. 验收标准（DoD）

1. unit：402 质询格式逐字段断言（字段名对齐 x402）；幂等重放不双计；验签各失败分支（签名错/过期/金额不符/nonce 重放）独立可测；影子闸门并发不超限；Provider 5xx/超时不产生 settle 记录；
2. 集成（mock provider 端点）：success / aborted（从未扣款）/ 402 / 409 四条路径端到端；
3. 并发压测小样本（同 consumer 50 并发调用）：无超扣（账本事务串行化验证）。

## 7.5 payment_scheme 接口（平台级抽象，铁律 P9 的收口处）

```python
class PaymentScheme(Protocol):
    name: str                                   # "erc3009-vault"（V1）
    def build_challenge(...) -> dict            # 402 质询（字段名对齐 x402）
    def verify(headers, service) -> VerifyResult  # 本地验签 + 链上约束检查
    def enqueue_settle(call, auth) -> None      # 写 settle 队列（scheme 无关，但入口在此）
    # settle 执行在 keeper（04），未来 scheme "eip3009-native" 时替换 verify/settle 目标
```
V1 仅实现 erc3009-vault；任何 x402 语义不得出现在网关其他模块。

## 8. 依赖与被依赖

- **依赖**：01 的 manifest 读取、03 的 api key↔消费者钱包绑定表、coincall-bot-chain-api 的 eth_call（/contracts/call）做链上约束检查；
- **被依赖**：06（读 calls/receipts）、04（读 calls 的 success 记录做结算依据）。
