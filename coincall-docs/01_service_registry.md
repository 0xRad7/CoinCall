# 01 — 管理面：服务目录与 Manifest（Provider 展示层）

> 前置阅读：00 总览（术语/铁律）。自包含：本文档足以独立实现本平面。

## 1. 职责边界

**做**：Provider 注册流程、ServiceManifest 数据模型与存储、机读目录 API、人读商店/排行榜视图（数据来自 06）、manifest hash 上链锚定。
**不做**：402 拦截与计费（02）、本地钱包与 api key 签发（03）、链上签名（全部经 coincall-bot-chain-api，见 05）。

## 1.5 上游凭证（endpoint credentials，2026-10-06 增补）

**问题**：http_json 型服务的三方上游常要求认证头（如 `X-API-KEY: sk-…`），而 manifest 是公开的（catalog 任何人可读）——凭证绝不能进 manifest。

**抽象**：公开契约与上游凭证分离——
- manifest（冻结契约）**零改动**：继续只描述 url/超时/计价；
- `service_credentials`（按 service_id 键控）：core 侧 Fernet 加密落盘，端点：
  - `PUT /services/{service_id}/credentials` `{headers: {名: 值}}`（全量替换，404=服务不存在，422=非法头名）
  - `GET /services/{service_id}/credentials` → **只回头名**（值永不回显）
  - `DELETE /services/{service_id}/credentials`
  - `GET /internal/services/{service_id}/credentials` → 解密值（网关取用，本机管理面 posture）
- 网关转发（http_json 型）：60s TTL 取凭证 → 注入上游；**消费者入站头一律不透传**；凭证获取失败按无凭证转发（上游 401 → aborted 零扣款）。

**信任模型（诚实边界）**：Provider 的上游密钥必然交给代理方——这是 Kong/Apigee 同款的 API 网关模型，与消费者资金钥匙是两个信任域，铁律 P7 不受影响。不想交出三方 key 的 Provider：自包一层薄适配服务（internal 或自有 http 端点）再上架。

## 2. Provider 接入流程（含链上步骤，全部经 coincall-bot-chain-api）

```
① 注册 Agent 身份（P4 强身份）
   POST coincall-bot-chain-api /api/v1/agent-identity/register
       {owner: <provider 的 keystore 账户地址>, agent_uri, dry_run:false}
   → 返回 {tx_hash, status}；agentId 待回执解析（见 05-X：需补"注册结果解析"端点）
② 绑定收款钱包（agentWallet）
   POST coincall-bot-chain-api /api/v1/agent-identity/{agentId}/wallet     ← ★ 05 新增端点 A
       {owner, wallet_address, dry_run:false}
③ 在本服务登记 Provider
   POST /providers {agent_id, display_name, contact?}
④ 发布服务（可多条）
   POST /services  （manifest，见 §3）
⑤ （可选，P2）manifest hash 上链锚定
   POST coincall-bot-chain-api /api/v1/agent-identity/{agentId}/metadata   ← ★ 05 新增端点 B
       {key: "service_manifest", value: <manifest_json>, dry_run:false}
```

说明：②④ 的签名为 coincall-bot-chain-api 的 keystore 代管账户或出资账户（其现有 TxService 语义）；provider 若自带 EOA，owner 填自己地址并由其自行确认（演示期简化为代管）。

## 3. ServiceManifest 数据模型（核心契约）

```json
{
  "service_id": "svc_translate_v1",          // 服务方自定义 slug，全局唯一
  "name": "中英技术文档翻译",
  "description": "面向技术文档的中英互译，保留 Markdown 结构",
  "version": "1.0.0",
  "provider": {
    "agent_id": 137,                          // 8004 tokenId
    "wallet": "0x…",                          // agentWallet（已绑定校验）
    "display_name": "Team booth-demo"
  },
  "endpoint": {
    "type": "http_json",                      // V1 仅支持这一种：POST JSON→JSON
    "url": "https://team-a.example/translate", // Provider 自运营端点（网关代理调用）
    "timeout_ms": 30000
  },
  "pricing": {
    "model": "per_call",
    "amount": "0.01",                         // USDT 人类可读
    "amount_raw": "10000"                     // 6 位精度最小单位（权威值）
  },
  "input_schema": { ...JSON Schema... },      // 请求体校验
  "output_schema": { ...JSON Schema... },
  "status": "active",                         // active|paused|delisted
  "created_at": "2026-10-05T…"
}
```

约束：
- `amount_raw` 为权威，`amount` 仅展示；两者一致性校验（wei_from_decimal 口径，见铁律 P3）；
- 发布时校验 `provider.wallet` == coincall-bot-chain-api `GET /agent-identity/{agent_id}` 返回的 agent_wallet（**防止收款地址未绑定**）；
- `input_schema` 用于 02 网关在转发前校验 Consumer 请求体（fail fast，不合规请求不产生计费）。

## 4. 存储（DuckDB，本服务自有库）

```sql
CREATE TABLE providers (
  agent_id    BIGINT PRIMARY KEY,      -- 8004 tokenId
  display_name VARCHAR,
  wallet      VARCHAR,                 -- 冗余缓存，发布时以链上查询为准
  created_at  TIMESTAMP DEFAULT now()
);
CREATE TABLE services (
  service_id  VARCHAR PRIMARY KEY,
  manifest    JSON,                    -- 完整 manifest 原文
  status      VARCHAR DEFAULT 'active',
  manifest_hash VARCHAR,               -- sha256(canonical json)，锚定用
  created_at  TIMESTAMP DEFAULT now(),
  updated_at  TIMESTAMP DEFAULT now()
);
```

## 5. 本平面 API（管理面自身的 REST）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/providers` | 登记 provider（校验 agent_id 在链上存在：经 coincall-bot-chain-api identity 视图） |
| POST | `/services` | 发布服务（§3 全量校验；返回 manifest_hash） |
| GET | `/services` | 机读目录：`?status=active`，返回 manifest 列表（Agent 发现服务的入口） |
| GET | `/services/{id}` | 单个 manifest（网关运行时也用，可 60s 内存缓存） |
| PATCH | `/services/{id}` | 改价/暂停（owner 鉴权：调用方 api key 绑定的 provider 身份） |
| GET | `/providers/{agent_id}/services` | 某 provider 的服务列表 |

机读目录响应附 `ETag`（目录版本），SDK 据此增量刷新。

## 6. 人读视图（商店页）

- 数据全部来自 06 的视图（调用量/收入/状态），本平面只做展示端点 `GET /store`（或直接前端页面，P1 简化为一个 JSON 渲染页）；
- 排行列：收入（最硬信誉）> 调用量 > 最近上线。

## 7. 验收标准（DoD）

1. 上述 API 全部有 unit 测试（manifest 校验/权限/一致性）；
2. 集成测试（needs_funds 级）：走完 §2 五步真实注册一个 provider 并发布服务，manifest 可从 `GET /services` 机读获取；
3. 非法 manifest（未绑定 wallet、amount 精度不符、schema 缺失）拒绝并返回三段错误模型风格错误。

## 8. 依赖与被依赖

- **依赖**：05 端点 A（setAgentWallet）、端点 B（metadata，P2）、coincall-bot-chain-api 既有 identity 视图；
- **被依赖**：02 网关（读 services 表/manifest）、06 排行榜（join providers）。
