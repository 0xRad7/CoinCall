# CONSTRAINTS.md — 研发规范与约束（活文档）

> 本文档先于一切功能编码定稿，是 bot_chain_api 的**唯一规范来源**。
> 任何违反规范的事实都必须沉淀到 §D；规范演进必须以追加条款方式修改，不允许静默删改。
> 关联：`pyproject.toml`（lint/类型/测试配置唯一来源）、`RESULTS.md`（验收与偏差）。

---

## §A 铁律（违反任何一条 = 返工）

| # | 铁律 | 落地点 |
|---|---|---|
| A1 | 默认测试网 Chain ID 968（`https://rpc.bohr.life/`）；主网 677 仅 `BOT_CHAIN_NETWORK=mainnet` **且** `BOT_CHAIN_ALLOW_MAINNET=1` 双重锁，默认禁用 | `core/config.py` |
| A2 | 不依赖任何官方 REST（Agent Wallet/Identity REST 未上线）：身份走 ERC-8004 三合约直调，智能账户走 4337 EntryPoint v0.7 + Bundler | 模块实现 |
| A3 | 合约地址/端点**只存在于 `app/core/chains.py`**，禁止在任何其他文件硬编码地址或 RPC URL | code review + grep 检查 |
| A4 | 写接口一律 `dry_run=true` 默认返回未签名预览；真实发送需显式 `dry_run=false`；私钥只从环境变量 `BOT_CHAIN_TEST_PRIVATE_KEY` 读取（仅允许测试网私钥），接口/日志永不回显 | `core/tx.py` + `core/log.py` |
| A5 | 测试先行：每个模块先写测试再写实现；`needs_funds` 用例无私钥时显式 skip（附领水指引 `https://faucet.bohr.life/basic`），skip 不算红 | `tests/` + 三级 marker |
| A6 | gas 恒定 20 gwei（baseFee=0）、出块约 1 秒、公共 RPC 无 debug/trace 方法——禁止以太坊式动态费用与深度追踪；链为 POA（extraData 277B），web3 实例必须经 `make_web3` 工厂装配 POA 中间件（C-04） | `core/rpc.py` |
| A7 | 服务运行期网络访问仅限 `*.bohr.life` 域与 faucet 探测；不主动访问 `botchain.ai` 主网域（除非显式主网模式+代理） | `core/rpc.py` |
| A8 | 不改动 `bot_chain_scripts/`、`BOT_CHAIN_REPORT.md`、`submission/`、`docs/`；蓝图与真实链行为冲突时以实测为准并记入偏差清单 | `RESULTS.md` 偏差节 |

## §B 工程规范

1. **工具链**：uv 管理环境与依赖；`pyproject.toml` 是唯一配置源（依赖 + ruff + mypy + pytest 全部收口，不另设 setup.cfg/.flake8/tox.ini）。
2. **静态检查**：见 §C，每轮强制。
3. **类型**：全量注解（公开函数/方法签名必须显式类型）；`# type: ignore` 每处必须带注释理由；禁止裸 `except:` 与吞异常（捕获必须带类型和处理/上抛）。
4. **API 边界**：请求/响应一律 pydantic 模型，路由不返回裸 dict；错误统一走 `core/errors.py` 三段模型（`chain_error`→502 / `tx_reverted`→409 / `service_error`→422），未处理异常→500，pydantic 校验→422；响应不得泄漏原生异常栈。
5. **结构**：web3/httpx 客户端经 FastAPI lifespan 构建与释放，依赖一律 `Depends` 注入；`modules/M*` 之间**禁止横向 import**，公共逻辑下沉 `core/`；单文件超过 ~400 行考虑拆分。
6. **日志**：结构化输出 + `trace_id` 贯穿请求/链调用；`private_key`、`Authorization`、`api_key` 类字段强制脱敏（`core/log.py` 统一实现）。
7. **测试质量**：unit 测试禁止真实网络（RPC/HTTP 全 mock，经 `dependency_overrides` / respx / fakeredis）；marker 已注册且 `--strict-markers` 生效；覆盖率门 `pytest -m unit --cov=app --cov-fail-under=80`。
8. **提交纪律**：Conventional Commits 且**必须带 scope**——`type(scope): 描述`，type ∈ feat|fix|refactor|test|docs|chore|perf|build|ci（环境 commit-msg 钩子机械校验，见 C-01）；实现与测试提交分离，一次提交一个意图；每阶段收尾打 tag（d0…d5）。
9. **写接口横切**：接受 `Idempotency-Key` 头防重放（Redis 或内存降级）；`dry_run=false` 且无私钥可用时报错不降级为 dry_run。

## §C 静态检查门（每轮代码编写强制）

```bash
ruff check . && ruff format --check . && mypy app
```

- 每写完一批代码（实现或测试）立即在仓库根执行上三连，**全过才允许提交**；
- `.githooks/pre-commit`（`git config core.hooksPath .githooks`）在每次 commit 时机械强制执行同样三连；钩子不过 = 提交被拒；
- 阶段收尾质量门追加：`pytest -m unit -q --cov=app --cov-fail-under=80`；
- 静态检查失败修复后，必须在 §D 追加沉淀条目（同 commit）。

## §D 犯错沉淀区（violation log）

格式：`C-NN | 日期 | 违反事实 | 根因 | 新增/强化约束`

| 编号 | 日期 | 违反事实 | 根因 | 新增/强化约束 |
|---|---|---|---|---|
| C-01 | 2026-10-01 | 首次提交消息 `chore: 初始化工程…` 被环境级 commit-msg 钩子拒绝 | 本机存在全局提交分型校验（typed-commit-discipline），要求 `type(scope): 描述`，scope 必填 | §B.8 强化：所有提交消息必须为 `type(scope): 描述` 形式（scope 必填，如 `feat(m5)`、`test(d1)`、`chore(build)`） |
| C-02 | 2026-10-01 | 首轮 ruff 报 199 项：中文全角标点（RUF001/002/003 ×176）与测试断言字面量（PLR2004 ×8）大量误报 | 任务钉死的 ruff 配置未考虑中文文档项目与测试语义：全角标点是中文文档正确写法；测试里 HTTP 状态码/链 ID 字面量即规格本身 | pyproject 增加 ignore RUF001/002/003 与 tests 豁免 PLR2004（带注释引用本条）；其余问题一律修代码不放宽 |
| C-03 | 2026-10-01 | `ExceptionRetryConfiguration(retries=2)` 抛 pydantic ValidationError（errors 字段拒绝 None） | web3 7.16 的该类 `__init__` 默认 errors=None 与 pydantic is-instance 校验冲突，必须显式传 errors=(ConnectionError, HTTPError, Timeout) | 凡遇第三方库"默认参数即崩"的行为，先查库内默认构造处照抄，再沉淀；禁止绕过（如捕获后吞掉） |
| C-04 | 2026-10-01 | D1 live 首跑 3 用例失败：`get_block` 抛 ExtraDataLengthError（extraData 277B） | **BOT Chain 实为 POA 链**，块头 extraData 超以太坊上限 32B；尽调脚本走裸 make_request 未暴露此问题 | 铁律 A6 补充：POA 适配（ExtraDataToPOAMiddleware）是 make_web3 的固定装配，任何新建 web3 实例必须走 make_web3 工厂，禁止直接 Web3(HTTPProvider(...)) |
| C-05 | 2026-10-01 | `make_request("debug_traceTransaction")` 未抛 Web3RPCError，用例 DID NOT RAISE 失败 | web3 7.16 make_request 对 JSON-RPC error 返回 `{"error": ...}` 字典，异常仅在高层 API 抛 | 对"预期错误"的断言必须同时接受「返回 error 字典」与「抛异常」两种形态（core 层封装时同样按此处理） |
| C-06 | 2026-10-01 | D3 补测时发现 TestM1ChainInfo 整类用例从未被执行（D2 覆盖率虚高） | `@respx.mock` 用作**类装饰器**会导致 pytest 收集不到该类任何用例（静默丢失，非报错） | 禁止类级 `@respx.mock`，一律方法级装饰；每阶段提交前用 `pytest --collect-only -q` 核对用例总数与文件内 `def test_` 数一致 |
| C-07 | 2026-10-01 | ABI 由 json.dumps 生成，文件中残留 `true/false` 字面量导致模块不可导入 | 生成 Python 数据文件不能用 JSON 序列化器直出 | 生成式资产（ABI 等）必须生成后立即 `importlib` 冒烟验证 |
| C-08 | 2026-10-01 | `Web3.to_hex(str)` / `Web3.to_bytes(hexstr=str)` 在运行时抛 TypeError（mypy 亦报） | web3 转换函数不接受裸 str | hex 串一律经 `core/rpc.hex_to_bytes`/`HexBytes()` 包装，模块禁止直接 `Web3.to_hex(字符串)` |
| C-09 | 2026-10-01 | D4 给 storage JSON 边界函数大量 ANN401 | redis/duckdb 封装天然透传任意 JSON | pyproject per-file-ignores 增加 `app/storage/**` 豁免 ANN401（JSON 序列化边界 Any 是正确类型） |
| C-10 | 2026-10-01 | unit 测试把 indexer 水位写进了持久 data/botchain.duckdb，且同步 TestClient 消费 SSE 全流挂满 300s | 测试 fixture 未隔离 app.state.store；TestClient.get 会等 StreamingResponse 整流结束 | unit 一律用 tmp_path DuckStore 替换 app.state.store（fixture 已内置）；SSE 端点只做路由存在性断言，全流验证放 live/脚本 |
| C-11 | 2026-10-01 | rm 删除 duckdb 主库后 WAL 残留，新进程重放 WAL 恢复了旧数据（status 出现脏水位） | DuckDB 的 WAL 与主库分离 | 清理 duckdb 必须主库+wal 成对删除；测试断言涉及库状态时先确认隔离 |

沉淀规则：
- 触发条件：静态检查被钩子拦截、阶段质量门红、bug 修复、实测链行为与编码假设不符、code review 发现的规范违反；
- 同类错误第二次出现：先改本规范（新增条款或强化已有条款），再修代码；
- 凡能被 lint/测试机械化的新约束，必须同步落成 ruff 规则（pyproject）或测试断言，而不是只写在这里。

## §E 偏差清单指针

与蓝图（`docs/bot_chain/`）冲突或用户指示覆盖的事项，统一记录在 `RESULTS.md` §偏差清单，本文件不重复维护；规范层面有影响的偏差需在 §B 对应条款处加注。

---

*当前版本 v1 · 定稿于 D0 阶段（2026-10-01）*
