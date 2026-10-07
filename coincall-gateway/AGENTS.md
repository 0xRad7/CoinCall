# AGENTS.md — coincall-gateway 子智能体入口

## 服务身份

- **coincall-gateway**：CoinCall 数据面 402 付费网关（FastAPI + uv + DuckDB），端口 **8030**。
- 职责：`POST /call/{service_id}` 7 步时序（认证/manifest/schema/幂等/支付+闸门/转发/收据）、
  calls 流水与 settle_queue 落库。
- 冻结契约点（08 §2，签名不得改动，问题上报不绕过）：
  - `app/core/payment.py`（X-PAYMENT + EIP-712 digest + recover）
  - `app/core/schemes.py`（PaymentScheme / ChainAdapter Protocol + registry）
  - `app/modules/calls.py`（calls / settle_queue 模型 + DDL）

## 全量测试入口（本仓门禁）

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app   # 静态三连
uv run pytest -q -m unit --cov=app --cov-fail-under=80                   # 单测 + 覆盖率 ≥80%
uv run pytest -q -m live                                                 # 唯一 live 冒烟（rpc.bohr.life）
```

## 绑定校验入口（制品链对账）

- 制品链宿主：`../coincall-core/`（intents/ specs/ plans/ tests/TEST-*.md，对本任务**只读**）。
- 当前活动链：`20261005202947`（CoinCall P0）；绑定索引：`../coincall-core/tests/TEST-20261005202947-coincall-p0.md`。
- 本仓承担的可执行用例绑定：**T11**（`tests/test_payment.py`）/ **T12**（`tests/test_shadow_gate.py`）/
  **T13**（`tests/test_calls_store.py`）/ **T14**（`tests/test_payment.py::test_golden_vector`，对 SPEC-D4）。
- 黄金向量：`tests/vectors/eip712_golden_gateway.json`（独立生成；W10 与 coincall-contracts 侧逐字节比对）。
- 规范正文：`../coincall-docs/`（只读）。

## 启动

```bash
uv sync
cp .env.example .env
uv run uvicorn app.main:app --host 0.0.0.0 --port 8030
```

依赖 coincall-core（8020）先行启动（apikey 校验与 manifest 读取）。
DuckDB 单写者纪律见 `CONSTRAINTS.md` A1。

## 工程纪律

见 `CONSTRAINTS.md`（静态三连 / 分型提交 / DuckDB 单写者 / 零网络单测 / fail-closed / 偏差记录区）。
