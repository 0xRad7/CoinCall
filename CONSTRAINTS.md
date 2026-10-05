# CONSTRAINTS.md — coincall-core 研发规范（精简移植自 coincall-bot-chain-api）

> 本文件是 coincall-core 的规范来源；与 `pyproject.toml`（lint/类型/测试唯一配置源）配套。
> 违反规范的事实必须沉淀到 §D；规范演进以追加条款方式修改，不允许静默删改。

## §A 铁律（违反 = 返工）

| # | 铁律 | 落地点 |
|---|---|---|
| A1 | **DuckDB 单写者**：`data/core.duckdb` 同一时刻只被一个进程持有；进程内单连接 + 锁串行化写；跑测试前确认无本服务进程占用（继承 bot-chain-api C-14） | `app/storage/db.py` |
| A2 | **零网络单测**：unit 测试禁止真实网络；DuckDB 一律 `tmp_path` fixture 隔离（继承 C-10/C-11） | `tests/conftest.py` |
| A3 | API 边界：请求/响应一律 pydantic 模型或显式契约 dict；错误统一三段模型 `{error, detail, code, trace_id}`（`trace_id` 贯穿） | `app/core/errors.py` |
| A4 | api key 明文只在签发响应回显一次；落库只存 sha256 hash | `app/modules/apikey.py` |
| A5 | 契约冻结点 `app/modules/manifest.py`：签名一经测试冻结不得改动，其他任务只 import 不修改，发现问题上报（08 §2） | `app/modules/manifest.py` |
| A6 | `pricing.amount_raw` 是权威值，`amount` 仅展示，二者按 token 精度强校验（铁律 P3） | `app/modules/manifest.py` |

## §B 工程规范

1. **工具链**：uv 管环境与依赖；`pyproject.toml` 是唯一配置源（依赖 + ruff + mypy + pytest 全收口，版本钉法对齐 coincall-bot-chain-api）。
2. **静态三连**（每批代码写完立即执行，全过才提交）：

   ```bash
   uv run ruff check . && uv run ruff format --check . && uv run mypy app
   ```

   `.githooks/pre-commit`（`git config core.hooksPath .githooks` 已启用）在每次 commit 机械强制同样三连。
3. **类型**：`app/` 全量注解（`disallow_untyped_defs`）；`# type: ignore` 必须带理由注释。
4. **测试**：测试先行、分型提交（test/impl 分开，一次提交一个意图）；质量门 `uv run pytest -q -m unit --cov=app --cov-fail-under=80`。
5. **提交纪律**：Conventional Commits 且必须带 scope——`type(scope): 描述`。
6. **服务端口 8020**；网关（coincall-gateway，8030）经 `/internal/apikeys/validate` 与 `GET /manifests/{id}` 读本服务。

## §C 静态检查门（每轮代码编写强制）

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run pytest -q -m unit --cov=app --cov-fail-under=80
```

## §D 犯错沉淀区（violation log）

| C-03 | 2026-10-05 | 预防性整改：与 gateway 同源的 >= 工具链下限约束（gateway 侧已实际翻车，见其 C-06） | 同模板建仓复制了浮动版本约束 | 工具链全部钉死 ==；与 gateway 保持同版本基线；升级=显式决策+全量重跑 |
格式：`C-NN | 日期 | 违反事实 | 根因 | 新增/强化约束`

| 编号 | 日期 | 违反事实 | 根因 | 新增/强化约束 |
|---|---|---|---|---|
| C-01 | 2026-10-05 | 建仓首轮脚本给测试文件插 `pytestmark` 静默未命中（脚本替换无 assert） | 单行 docstring 使锚点判断永假 | 脚本化文本替换必须 assert 命中且失败即停（继承 bot-chain-api C-20） |
| C-02 | 2026-10-05 | manifest JSON 列经 `TO_JSON(?)` 双重编码，读回变成 str | duckdb 的 TO_JSON 对 VARCHAR 参数产 JSON 字符串标量 | JSON 列写入用 `CAST(? AS JSON)`，不用 TO_JSON 包参数 |

## §E 偏差清单

- provider.wallet 的链上绑定校验（01 §3）与 `GET /services` 目录语义、PATCH 改价、`/providers` 端点：P1-2 范围，本仓 P0 只交付 `POST /manifests` / `GET /manifests/{id}` / `GET /catalog`。→ P1-2 已落地 `/providers` 登记（含链上身份存在性校验）；`GET /services` 目录语义由既有 `/catalog` 承担；PATCH 改价仍留 P2。
- EVM 地址仅做格式校验（无 web3 依赖）；EIP-55 checksum 与链上绑定校验留待 P1-2 经 bot-chain-api。→ P1-2 维持零 web3：链上事实全部经 8010（identity 视图 + `/contracts/logs` 透传），本仓不引链库。
- **实测契约（P1-2，2026-10-01）**：8010 identity 视图对未注册 tokenId 返回 **409 tx_reverted**（ownerOf revert，detail 含"不存在或未注册"），并非任务书假设的 404——本仓 identity 客户端将 404 与"不存在"语义的 409 均映射为 422 `identity_not_found`，其余 409 仍按 502 `identity_unavailable` 上抛。
- P1-3 榜单收入源：06 §3 的 v_service_summary/v_provider_credit 视图原设计基于本仓 calls 表，但 v2 口径（09 P1-3/T23 汇合门）收入以**链上 Charged 为唯一真相**——本仓无 calls 表（流水在网关），故收入侧落 charged_events 库存表（水位增量拉取），活跃度侧读网关 `/internal/stats/calls`；视图 SQL 语义由 Python 聚合等价承载。
