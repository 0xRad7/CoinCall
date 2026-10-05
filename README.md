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

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/call/{service_id}` | 统一付费调用入口（7 步时序；200/401/402/404/409/422/502） |
| GET | `/healthz` | 存活探针 |

请求头：`X-Api-Key`（core 签发）、`X-PAYMENT`（base64 JSON：from/to/value/validAfter/
validBefore/nonce/v/r/s）、`X-Idempotency-Key`（可选）。成功响应附
`X-Receipt-Id / X-Charged-Raw / X-Receipt-Sig`。

## 模块地图（四个冻结契约点见 08 §2）

| 文件 | 角色 |
|---|---|
| `app/core/payment.py` | **冻结契约**：X-PAYMENT 解析 + EIP-712 digest + ecrecover |
| `app/core/schemes.py` | **冻结契约**：PaymentScheme/ChainAdapter Protocol + PayVaultScheme + registry |
| `app/modules/calls.py` | **冻结契约**：calls/settle_queue 模型 + DDL + CallStore |
| `app/core/shadow_gate.py` | 影子闸门（K=3，fail-closed） |
| `app/core/chain.py` | BotChainAdapter（web3 只读 eth_call，30s 短缓存） |
| `app/modules/call_route.py` | 7 步时序编排 |
| `app/modules/auth.py` / `manifest_client.py` | core 管理面客户端（apikey 校验 / manifest 缓存） |
| `app/modules/providers.py` | ProviderAdapter（InternalEchoProvider / HttpJsonProvider） |
| `app/modules/receipt.py` | 收据 + HMAC 防签 |

黄金向量（EIP-712，独立生成，W10 与合约侧逐字节比对）：
`tests/vectors/eip712_golden_gateway.json`。

## 测试与门禁

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run pytest -q -m unit --cov=app --cov-fail-under=80
uv run pytest -q -m live          # 唯一 live 冒烟：rpc.bohr.life 一次 eth_call
```

绑定用例（TEST-20261005202947）：T11 `test_payment.py` / T12 `test_shadow_gate.py` /
T13 `test_calls_store.py` / T14 `test_payment.py::test_golden_vector`。制品链宿主在
coincall-core 主仓（本仓只读引用）。规范正文：`../coincall-docs/`（只读）。
