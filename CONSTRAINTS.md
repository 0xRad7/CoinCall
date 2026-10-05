# CONSTRAINTS.md — coincall-gateway 研发规范（精简移植自 coincall-bot-chain-api）

> 本文件是 coincall-gateway 的规范来源；与 `pyproject.toml`（lint/类型/测试唯一配置源）配套。
> 违反规范的事实必须沉淀到 §D；规范演进以追加条款方式修改，不允许静默删改。

## §A 铁律（违反 = 返工）

| # | 铁律 | 落地点 |
|---|---|---|
| A1 | **DuckDB 单写者**：`data/gateway.duckdb` 同一时刻只被一个进程持有；进程内单连接 + 锁串行化；跑测试前确认无本服务进程占用（继承 bot-chain-api C-14） | `app/modules/calls.py` |
| A2 | **零网络单测**：unit 测试禁止真实网络；链上/mock 经 `_raw_call` 注入、core 客户端经 respx（**仅方法级装饰器**，继承 C-06）；DuckDB 一律 tmp_path（C-10/C-11） | `tests/` |
| A3 | **唯一 live 冒烟**：对 `rpc.bohr.life` 一次只读 eth_call，打 `live` 标记；其余一切网络访问（http_json provider 真外联）属 W4+ 接线，默认不进测试 | `tests/test_chain.py` |
| A4 | **三个冻结契约点**（08 §2）：`app/core/payment.py` / `app/core/schemes.py` / `app/modules/calls.py` 的签名一经测试冻结不得修改；其他任务只 import；发现问题上报而非绕过 | 对应文件 |
| A5 | 黄金向量**独立生成**（SPEC-D4）：`tests/vectors/eip712_golden_gateway.json`；W10 汇合时与 coincall-contracts 侧逐字节比对，不一致即冻结失败回上游 | 向量文件 + `test_payment.py::test_golden_vector` |
| A6 | **影子闸门 fail-closed**：链上额度未知（None）一律拒绝；拒绝即 402，绝不放行不确定请求（02 §5c） | `app/core/shadow_gate.py` |
| A7 | **Provider 失败不收钱**：非 2xx/超时 → calls=aborted、零 settle 记录（02 时序⑥，无裁判体系下对 Consumer 的唯一保障） | `app/modules/call_route.py` |
| A8 | 任何 x402 语义只允许出现在 scheme 层（02 §7.5）；`COINCALL_PAY_VAULT_ADDRESS` 等合约地址只走 env/config，不散落硬编码 | `app/core/schemes.py` / `config.py` |
| A9 | 私钥纪律：代码与测试只持有**测试助记词**派生账户；生产消费者私钥永不出消费者本地（P1-1 SDK 范围） | `tests/conftest.py` |

## §B 工程规范

1. **工具链**：uv；`pyproject.toml` 唯一配置源（版本钉法对齐 coincall-bot-chain-api）。
2. **静态三连**（每批写完立即执行，全过才提交；`.githooks/pre-commit` 机械强制）：

   ```bash
   uv run ruff check . && uv run ruff format --check . && uv run mypy app
   ```

3. **类型**：`app/` 全量注解；`# type: ignore` 必须带理由。
4. **测试**：测试先行、分型提交（test/impl 分开）；质量门 `uv run pytest -q -m unit --cov=app --cov-fail-under=80`。
5. **提交纪律**：Conventional Commits 带必填 scope：`type(scope): 描述`。
6. **服务端口 8030**；core（8020）经 `/internal/apikeys/validate` 与 `GET /manifests/{id}` 读取。

## §C 静态检查门（每轮代码编写强制）

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run pytest -q -m unit --cov=app --cov-fail-under=80
```

## §D 犯错沉淀区（violation log）

| C-06 | 2026-10-05 | 交接态复跑 ruff check 红（I001 import 排序），W3 汇报『全绿』失真 | 工具链 >= 下限约束（ruff 0.6→0.16.10 isort 行为漂移）+ ruff 缓存掩盖首跑结果；交接前未复跑 | pyproject 工具链全部钉死 ==；汇报门禁前必须当场复跑并以输出为准；升级工具链=显式决策+全量重跑 |
| 编号 | 日期 | 违反事实 | 根因 | 新增/强化约束 |
|---|---|---|---|---|
| C-01 | 2026-10-05 | 影子闸门首批用例用不同 key_id 断言 K 上限，永远绿不了 | K 是**每 key** 在途上限，测试语义写错 | 闸门用例必须同一 key 连续在途再断言第 K+1 笔拒绝 |
| C-02 | 2026-10-05 | live 冒烟 `int(raw,16)` 崩：async web3 `eth.call` 返回 bytes 而非 HexStr | 同步/异步路径返回类型不一致 | `_raw_call` 统一归一 hex 字符串（bytes→hex），下游只处理 str |
| C-03 | 2026-10-05 | `pytestmark = pytest.mark.unit` 模块级打在含 live 用例的文件上，live 被误选入 unit | pytestmark 对全模块追加 marker | 混合 marker 的文件不设模块级 pytestmark，逐用例打标 |
| C-04 | 2026-10-05 | FakeAuth 以 `keys_by_key or default` 兜底，空 dict 被当成"未传" | falsy 判断误伤显式空表 | 注入型 fake 的"未传"判断一律 `is None`，禁用 truthiness |
| C-05 | 2026-10-05 | `from __future__ import annotations` 插到 docstring 之前，docstring 失效且 E402 全红 | future import 必须在模块 docstring 之后、其他代码之前 | 脚本插 import 先确认锚点顺序；TYPE_CHECKING 引用必须配 future annotations |

## §E 偏差清单

- nonce 防重放为内存 set（02：Redis 记录）；Redis 镜像、幂等 24h 持久化属 P2。
- ~~settle 队列的消费者（keeper）是 W5，本仓只写不消费~~ → W5/P0-5 已落地（app/modules/keeper.py，2026-10-06）。
- HttpJsonProvider 已实现但默认单测全 mock；真实外联与 PayVault 地址接线属 W4。
- 402 质询 `topup.deposit_address` 暂用 vault 地址占位（04 §2 平台收款地址属 W4+）。
- keeper 拉黑联动为**最小实现**（P0-5）：core 无 apikey 挂起/拉黑端点（读 core 仓确认），
  故坏账消费者只进 gateway 进程内存黑名单并在 `/call` 认证步拦截（402 code=bad_debt）；
  重启即清空、core 侧不知情。P2：core 增挂起端点 + 持久化 bad_debt 表。
- keeper `transfer_failed` 重试计数在内存（与 nonce set 同口径）：keeper 重启后计数重置，
  最坏情况多重试一轮（nonce 未烧，安全）。
- ChargeFailed 事件**不带 nonce**（合约契约）：批内按 `from` 地址归属失败行；
  同批同消费者多笔失败时按提交顺序一对一归属（演示规模下无歧义）。
- keeper 的 provider 记账钱包解析：静态覆盖（`COINCALL_KEEPER_PROVIDER_WALLET_OVERRIDES`，
  agent_id→wallet）优先，缺省走 core manifest（calls.service_id → provider.wallet）；
  两者都失败该行保持 pending 并告警，不丢单。
