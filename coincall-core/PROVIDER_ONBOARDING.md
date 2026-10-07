# Provider 接入指南（五步上架即售）

> 对应规范：coincall-docs/01 §2（接入流程）/ §5（API）。端点对照表见文末。
> 范围铁律：真实第三方 Provider 的选品/预集成不在本期范围——本指南只保证
> "接入层抽象"就位，任何按契约挂上的 Provider 即可被目录发现、被网关售卖、按链上收入上榜。

## 前置

- 三个服务在跑：bot-chain-api `:8010` / core `:8020` / gateway `:8030`；
- 计价 token：USDT（6 位精度，BOT Chain 测试网 968）；收款钱包 = ERC-8004 agentWallet；
- 写链操作（步骤 ①②）走 bot-chain-api 的代管账户出资（`dry_run:false` 真实发送）。

## 五步

### ① 注册 Agent 身份（ERC-8004）

```bash
curl -X POST http://127.0.0.1:8010/api/v1/agent-identity/register \
  -H 'Content-Type: application/json' \
  -d '{"owner":"<provider 的 keystore 账户地址>","agent_uri":"https://example/agent","dry_run":false}'
# → {tx_hash, ...}；约 1 个块后解析回执拿 agentId：
curl http://127.0.0.1:8010/api/v1/agent-identity/register-result/<tx_hash>
# → {found, status, agent_ids:[162,...], owner, agent_wallet}
```

### ② 绑定收款钱包（agentWallet）

```bash
curl -X POST http://127.0.0.1:8010/api/v1/agent-identity/<agentId>/wallet \
  -H 'Content-Type: application/json' \
  -d '{"owner":"<owner>","wallet_address":"<收款地址>","signature":null,"deadline":<now+300s内>,"dry_run":false}'
```

签名语义（bot-chain-api C-23 定案）：EIP-712 v4，**newWallet 本人签名**
（ECDSA 恢复 == newWallet；无签名时由服务代签路径处理，空签名必 revert）。
绑定结果可验证：`GET :8010/api/v1/agent-identity/<agentId>` 的 `agent_wallet`。

### ③ 在 core 登记 Provider

```bash
curl -X POST http://127.0.0.1:8020/providers \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":162,"display_name":"Team booth-demo"}'
# → {agent_id, display_name, wallet(=链上 agentWallet 回读), created_at}
# agent_id 未在链上注册 → 422 identity_not_found（结果短缓存 60s）
```

### ④ 发布服务（ServiceManifest）

```bash
curl -X POST http://127.0.0.1:8020/manifests -H 'Content-Type: application/json' -d '{
  "service_id": "svc_translate_v1",
  "name": "中英技术文档翻译",
  "version": "1.0.0",
  "provider": {"agent_id":162,"wallet":"<agentWallet>","display_name":"Team booth-demo"},
  "endpoint": {"type":"http_json","url":"https://team-a.example/translate","timeout_ms":30000},
  "pricing": {"model":"per_call","token":"USDT","amount":"0.01","amount_raw":"10000"},
  "chain": {"network":968},
  "input_schema": {"type":"object","properties":{"text":{"type":"string"}}},
  "output_schema": {"type":"object"}
}'
# → {service_id, status, manifest_hash, manifest}
```

校验铁律：`amount_raw` 为权威（6 位精度最小单位），`amount` 仅展示且需一致；
`endpoint.type` 仅 `http_json`（网关代理转发）或 `internal`（平台内置 demo）；
`chain.network` V1 恒 968；schema 必须含 `type` 关键字。非法 manifest → 422 三段错误。

### ⑤ 上架即售（目录发现 + 收入上榜）

- 机读目录（Agent/SDK 发现入口，响应附 ETag）：`GET :8020/catalog?status=active`
- 消费者付费调用：`POST :8030/call`（X-Api-Key + X-PAYMENT，网关校验 manifest.input_schema 后转发）
- 收入榜（链上 Charged 唯一真相，收入优先排序）：`GET :8020/leaderboard/providers`
- 链上 proof（每个数字可核）：`GET :8020/leaderboard/providers/<wallet>/proof`
  → 该 provider 全部 Charged 交易哈希清单（scan.bohr.life 逐笔可查）

可选（P2）：manifest hash 上链锚定 `POST :8010/api/v1/agent-identity/<agentId>/metadata`
（`{"key":"service_manifest","value":<manifest_json>}`）。

## 端点对照表（01 §2 五步 ↔ 实际端点）

| 步骤 | 规范端点（01/05 篇） | 实际调用 | 状态 |
|---|---|---|---|
| ① 注册身份 | POST bot-chain-api /agent-identity/register | `POST :8010/api/v1/agent-identity/register`（dry_run:false）+ `GET /agent-identity/register-result/{tx_hash}` 解析 agentId | 已有（P0-1/W1） |
| ② 绑定钱包（05 端点 A） | POST /agent-identity/{agentId}/wallet | `POST :8010/api/v1/agent-identity/{agentId}/wallet`（EIP-712 v4，newWallet 签名） | 已有（W1/C-23） |
| ③ 登记 Provider | POST core /providers | `POST :8020/providers`（先校验链上身份存在：404/409→422 identity_not_found） | 本期交付（T21） |
| ④ 发布服务 | POST core /services | `POST :8020/manifests`（upsert by service_id，返回 manifest_hash） | 已有（P0，§5 命名对齐 manifests） |
| ⑤ 目录/售卖/榜单 | GET /services；06 榜单 | `GET :8020/catalog`（ETag）；`POST :8030/call`；`GET :8020/leaderboard/*、/store、/stats/overview` | 目录已有；榜单本期交付（T22/T23） |
| 可选锚定（P2） | POST /agent-identity/{id}/metadata | `POST :8010/api/v1/agent-identity/{agentId}/metadata` | 未做（范围外） |

## 相关端点速查（core 8020）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/apikeys` | api key 列表（无明文/无 hash） |
| DELETE | `/apikeys/{key_id}` | 吊销（validate 即拒，网关侧立即生效；幂等） |
| PUT | `/apikeys/{key_id}/wallet` | 换绑消费者钱包（网关实时 validate，无缓存延迟） |
| GET | `/providers`、`/providers/{agent_id}/services` | Provider 列表 / 名下服务（manifests 关联） |
| GET | `/leaderboard/services`、`/leaderboard/providers` | 榜单（收入优先；收入=链上 Charged） |
| GET | `/store` | active 服务 × 收入聚合（商店页） |
| GET | `/stats/overview` | GMV/笔数/服务数/Provider 数（demo 大屏数字） |
| GET | `/leaderboard/providers/{wallet}/proof` | 该 provider 全部 Charged 交易哈希清单 |
