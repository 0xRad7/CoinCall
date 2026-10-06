# coincall-gateway — CoinCall 数据面（402 付费网关）

FastAPI + uv + DuckDB 的**数据面**运行时：`POST /call/{service_id}` 按 02 节 7 步时序
完成 api key 认证 → X-PAYMENT 解析与 EIP-712 验签 → 链上约束 → 影子闸门（K=3）→
schema 校验 → Provider 转发 → 回执 + settle 队列落库。端口 **8030**。

## 快速启动

```bash
uv sync
cp .env.example .env          # 按需改 COINCALL_CORE_BASE_URL / COINCALL_PAY_VAULT_ADDRESS
uv run uvicorn app.main:app --host 0.0.0.0 --port 8030
```

数据落 `data/gateway.duckdb`（DuckDB 单写者：跑测试前勿同时运行本服务，见 CONSTRAINTS A1）。

## 演示快速启动（07 三幕剧本 / 09 P1-4）

三服务按序拉起（**由主线程/常驻会话拉起**——子智能体/临时会话拉起的进程会随会话被回收，
这是实测教训；8030 必须带 keeper env）：

```bash
# 终端 1：coincall-bot-chain-api（8010）   —— 在 ../coincall-bot-chain-api 下
uv run uvicorn app.main:app --port 8010
# 终端 2：coincall-core（8020）            —— 在 ../coincall-core 下
uv run uvicorn app.main:app --port 8020
# 终端 3：coincall-gateway（8030，keeper 开）
cd coincall-gateway && COINCALL_KEEPER_ENABLED=true uv run uvicorn app.main:app --port 8030
```

三幕彩排（无人工干预，逐步打印+断言+时间戳，自动录档 `results/demo_p1.md`）：

```bash
uv run python scripts/demo_p1.py
```

- 第一幕 挂服务（≤30s 口径）：登记 provider(162) → 发布 `svc_translate` / `svc_contract_scan` /
  `svc_chain_report` 三 manifest → catalog 可见；
- 第二幕 付费调用：SDK（`../coincall-sdk`，脚本自动 sys.path 引入）钱包导入 anvil#1 →
  三个服务各一笔真实付费 + 402 指引演练（未 approve 新 key → `PaymentRequiredError.guidance`
  → approve → 成功；注意 approve 后要等网关链上约束 30s 短缓存过期再重试）；
- 第三幕 结算与信誉：keeper 攒批上链（batch=3 / 30s 档）→ Charged 明细与交易哈希 →
  `providerWithdraw`（anvil#2 直签 raw tx）→ MockUSDT 到账增量==提取额 → 排行榜/proof；
- 兜底演练：svc_translate 三段热切换（友队 http_json → 掉线 502 零扣款 → 我方备用 →
  internal 兜底）；每次切换的传播时延 = 网关 manifest 缓存 TTL（默认 60s）。

内置 demo 服务（`app/modules/internal_services.py`）：manifest `endpoint.type=internal` 且
`url=internal://<name>` 时网关本地执行（不走外网）——`translate` 回显兜底 / `chain_report`
（8010 链健康 + core 总览 → 报告）/ `contract_scan`（8010 扫 PayVault Charged → 结构化摘要）；
无 url 或未知名 → 回显兜底。彩排勘误与工程坑（raw tx 必须带 `to`、SDK 幂等键跨进程重放等）
见 `results/demo_p1.md` 附注。

诚实边界：overview 的数字就是全部真实发生过的调用（07 §5，无 GMV 修饰）；anvil 私钥是
公开测试密钥，脚本与日志永不打印任何私钥；演示计价 MockUSDT 为测试网公开 mint。

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/call/{service_id}` | 统一付费调用入口（7 步时序；200/401/402/404/409/422/502） |
| GET | `/healthz` | 存活探针 |
| GET | `/internal/stats/calls` | 每服务聚合：窗口计数/p50/p95 延迟/去重付款人/最近活动（`?window_hours=` 默认 168、上限 720，10 §1 冻结契约） |
| GET | `/internal/receipts/pubkey` | 收据 Ed25519 公钥 hex（第三方离线验签入口，10 §2） |
| GET | `/internal/keeper/status` | keeper 可观测：队列深度 / 最近一批 / 累计 Charged / 黑名单 / anchor 段 |

请求头：`X-Api-Key`（core 签发）、`X-PAYMENT`（base64 JSON：from/to/value/validAfter/
validBefore/nonce/v/r/s）、`X-Idempotency-Key`（可选）。成功响应附
`X-Receipt-Id / X-Charged-Raw / X-Receipt-Ts / X-Receipt-Sig-Ed25519 / X-Receipt-Sig`。

## 收据双签（10 §2：Ed25519 自验签 + HMAC 过渡）

- **新口径（Ed25519）**：网关密钥对签名，覆盖规范串
  `receipt_id|service_id|amount_raw|status|ts`（ts=unix 秒，随 `X-Receipt-Ts` 头发布），
  签名在 `X-Receipt-Sig-Ed25519`（64 字节 hex）；任何持有公钥者（Agent/第三方/链下审计）
  从 `GET /internal/receipts/pubkey` 取公钥 hex 即可离线验证收据真伪，无需网关参与。
  密钥种子 `COINCALL_RECEIPT_SEED`（32 字节 hex）注入后跨重启复现；缺省每次启动随机
  生成并日志告警（旧收据随之不可验，生产务必注入）。
- **旧口径（HMAC，过渡窗口保留）**：`X-Receipt-Sig`（HMAC-SHA256，密钥在网关）继续
  双签下发，仅本机可验；消费方迁移到 Ed25519 后该头择期下线。
- CORS `expose_headers` 已含全部收据头（前端直连模式可读）。

## keeper 结算器（04 §3 / 09 P0-5）

进程内常驻 asyncio 任务（`COINCALL_KEEPER_ENABLED=true` 开启）：读 settle_queue
pending → 攒批（笔数 `COINCALL_KEEPER_BATCH_SIZE` 默认 3 / 时间
`COINCALL_KEEPER_FLUSH_INTERVAL` 默认 30s，单笔交易硬上限 50=合约 MAX_BATCH）→ 经
coincall-bot-chain-api `POST /contracts/send` 提交 `chargeWithSigBatch`
（from=operator，**由 bot-chain-api keystore 代管签名，本服务不持有任何私钥**）→
回执事件本地解码后分支落库：Charged→done/settled；`transfer_failed` 原签名重试一次
后置 bad_debt 并拉黑消费者（402 `bad_debt` 拦截后续调用）；`not_in_window`/`nonce_used`
→expired；`bad_signature`/`auth_to_mismatch`→failed+ERROR 告警。

**keeper 账户（operator `0xC37fFE…B63a`）需保持少量 BOT 付 gas**（每批一笔交易，
本链恒定 20 gwei，≈0.001 BOT 级）；资金划转在 MockUSDT 层完成，operator 与资金
安全无关（合约 I1）。kill 后重启：pending 笔自动续批；提交后宕机的笔经
`usedNonces` 只读探测补记，不重放不丢单。

## 决策摘要锚定任务（10 §1/§2）

keeper 启用时随进程常驻的独立协程（`COINCALL_ANCHOR_INTERVAL_S`，默认 1800s）：

1. `GET {core}/internal/decision/anchor-pending` 拉 core 聚合好的待锚摘要；
2. 对每条经 bot-chain-api `POST /api/v1/contracts/send` 调 ERC-8004
   `setMetadata(tokenId, "coincall:decision:v1", bytes)`（to=IdentityRegistry
   `0xec8fFb…99c0`，tokenId=provider 的 agent_id，value=`digest|pointer` 的 UTF-8
   bytes——**链上只放聚合摘要**，原始数据按 digest 在 core API 可核）；
3. 成功回 `POST {core}/internal/decision/anchor-result`；失败记日志下轮重试
   （幂等靠 core 的 anchor_records：缺回执即重发，setMetadata 同值覆盖天然幂等）。

- **gas 由出资账户（operator）承担**（每条摘要一笔交易 ≈0.0014 BOT，70k gas@20gwei
  量级）；提交仍走 bot-chain-api keystore，本仓不持私钥（铁律 P1）。
- core（8020）不可达只**降级为告警**（status 端点 anchor 段 `degraded:true`），
  与结算主循环互不阻塞；
- 锚定目标必须是 operator 名下（或获授权）的身份 token——`setMetadata` 合约侧要求
  owner/approved；provider 自持 token 的锚定需该 provider 自签或授权（诚实边界）。
- 真链实跑存档：`tests/test_anchor_live.py`（needs_funds）——720h 窗口真实统计
  digest 锚到 token 162，`getMetadata` 链上回读逐字节断言。

## 模块地图（四个冻结契约点见 08 §2）

| 文件 | 角色 |
|---|---|
| `app/core/payment.py` | **冻结契约**：X-PAYMENT 解析 + EIP-712 digest + ecrecover |
| `app/core/schemes.py` | **冻结契约**：PaymentScheme/ChainAdapter Protocol + PayVaultScheme + registry |
| `app/modules/calls.py` | **冻结契约**：calls/settle_queue 模型 + DDL + CallStore |
| `app/core/shadow_gate.py` | 影子闸门（K=3，fail-closed） |
| `app/core/chain.py` | BotChainAdapter（web3 只读 eth_call，30s 短缓存） |
| `app/core/abis/payvault.json` | PayVault ABI 只读副本（事实源：coincall-contracts 编译产物） |
| `app/modules/call_route.py` | 7 步时序编排（含 bad_debt 黑名单拦截） |
| `app/modules/auth.py` / `manifest_client.py` | core 管理面客户端（apikey 校验 / manifest 缓存） |
| `app/modules/keeper.py` | keeper 结算器（攒批/上链/回执分支/黑名单/恢复探测） |
| `app/modules/keeper_route.py` | `GET /internal/keeper/status`（含 anchor 段） |
| `app/modules/anchor.py` | 决策摘要锚定任务（anchor-pending→setMetadata→anchor-result） |
| `app/core/abis/erc8004.py` | IdentityRegistry setMetadata ABI 副本切片（锚定用） |
| `app/modules/stats_route.py` | `GET /internal/stats/calls`（窗口化扩展统计） |
| `app/modules/providers.py` | ProviderAdapter（InternalEchoProvider / HttpJsonProvider） |
| `app/modules/internal_services.py` | 内置 demo 服务（internal:// 分发 + 三 handler，09 P1-4） |
| `app/modules/receipt.py` | 收据 Ed25519 签名器 + HMAC 双签（10 §2） |
| `app/modules/receipts_route.py` | `GET /internal/receipts/pubkey` |

黄金向量（EIP-712，独立生成，W10 与合约侧逐字节比对）：
`tests/vectors/eip712_golden_gateway.json`。

## 测试与门禁

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run pytest -q -m unit --cov=app --cov-fail-under=80
uv run pytest -q -m live          # 唯一 live 冒烟：rpc.bohr.life 一次 eth_call
uv run pytest -q -m needs_funds   # 真链：keeper 批结算 + 决策摘要锚定（需 8010/8030 在线）
```

绑定用例（TEST-20261005202947）：T11 `test_payment.py` / T12 `test_shadow_gate.py` /
T13 `test_calls_store.py` / T14 `test_payment.py::test_golden_vector`；P1 链（TEST-20261005221052）
T24 = `scripts/demo_p1.py` 彩排录档 `results/demo_p1.md`（internal handlers 单测在
`tests/test_internal_services.py`）。制品链宿主在
coincall-core 主仓（本仓只读引用）。规范正文：`../coincall-docs/`（只读）。
