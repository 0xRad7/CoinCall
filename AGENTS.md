# AGENTS.md — coincall-core 子智能体入口

## 服务身份

- **coincall-core**：CoinCall 管理面（FastAPI + uv + DuckDB），端口 **8020**。
- 职责：ServiceManifest 目录（发布/查询/机读目录）、api key 签发与内部校验。
- 冻结契约点：`app/modules/manifest.py`（08 §2；一经测试冻结不得改签名，问题上报不绕过）。

## 全量测试入口（本仓门禁）

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app   # 静态三连
uv run pytest -q -m unit --cov=app --cov-fail-under=80                   # 单测 + 覆盖率 ≥80%
```

## 绑定校验入口（制品链对账）

- 制品链宿主即**本仓**：`intents/` `specs/` `plans/` `tests/TEST-*.md`（**只读**，属项目制品链，不许改动或删除）。
- 当前活动链：`20261005202947`（CoinCall P0）；绑定索引：`tests/TEST-20261005202947-coincall-p0.md`。
- 本仓承担的可执行用例绑定：**T9**（`tests/test_manifest.py`）/ **T10**（`tests/test_apikey.py`）。
- 规范正文：`../coincall-docs/`（对本仓只读）。

## 启动

```bash
uv sync
cp .env.example .env   # 可选
uv run uvicorn app.main:app --host 0.0.0.0 --port 8020
```

数据落 `data/core.duckdb`（单写者纪律见 `CONSTRAINTS.md` A1；跑测试前勿同时运行本服务）。

## 工程纪律

见 `CONSTRAINTS.md`（静态三连 / 分型提交 / DuckDB 单写者 / 零网络单测 / 偏差记录区）。
