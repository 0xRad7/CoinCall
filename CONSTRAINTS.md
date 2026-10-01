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
| A6 | gas 恒定 20 gwei（baseFee=0）、出块约 1 秒、公共 RPC 无 debug/trace 方法——禁止以太坊式动态费用与深度追踪 | `core/tx.py` |
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
8. **提交纪律**：Conventional Commits（feat/test/chore/docs 分型），实现与测试提交分离，一次提交一个意图；每阶段收尾打 tag（d0…d5）。
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
| （暂无——首个违规发生时在此追加） | | | | |

沉淀规则：
- 触发条件：静态检查被钩子拦截、阶段质量门红、bug 修复、实测链行为与编码假设不符、code review 发现的规范违反；
- 同类错误第二次出现：先改本规范（新增条款或强化已有条款），再修代码；
- 凡能被 lint/测试机械化的新约束，必须同步落成 ruff 规则（pyproject）或测试断言，而不是只写在这里。

## §E 偏差清单指针

与蓝图（`docs/bot_chain/`）冲突或用户指示覆盖的事项，统一记录在 `RESULTS.md` §偏差清单，本文件不重复维护；规范层面有影响的偏差需在 §B 对应条款处加注。

---

*当前版本 v1 · 定稿于 D0 阶段（2026-10-01）*
