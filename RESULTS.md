# RESULTS.md — 验收与偏差总报告

> 交付日期 2026-10-01 · 工作区 `bot_chain_api/` · 规范见 [CONSTRAINTS.md](CONSTRAINTS.md)

## 一、验收门状态（G1–G5）

| 门 | 标准 | 状态 | 证据 |
|---|---|---|---|
| G1 协议冻结 | test_01/02 live 全绿 + 结果归档 | ✅ **PASS** | `results/g1_live_2026-10-01.{xml,json}`（14/14，2026-10-01） |
| G2 读能力 | test_04/06/07/08 live 部分全绿 | ✅ **PASS** | test_04(5) + test_06(4) + test_07(4) + test_08(5) live 全绿 |
| G3 写能力 | needs_funds 全绿（资金到位后 ≤30min） | ⏸ **待资金** | 9 个 needs_funds 用例已就绪，无私钥时显式 skip；领水后 `pytest -m needs_funds` 补跑 |
| G4 服务 | unit 全绿 + /docs 可交互 | ✅ **PASS** | unit 103 通过/覆盖 81.23%；`/docs` 200、55 路径（uvicorn 实测存证于 D4 提交说明） |
| G5 演示 | 05 篇 15 步剧本全程跑通 | ✅ **PASS（无资金口径）** | `results/g5_demo.md`：11 PASS + 4 NEEDS_FUNDS + 0 FAIL |

## 二、needs_funds 待补跑清单（领水后 30 分钟内）

前置：到 <https://faucet.bohr.life/basic> 领水（每次 10 BOT + 1000 tUSDT，冷却约 10 分钟），
私钥写入 `.env` 的 `BOT_CHAIN_TEST_PRIVATE_KEY` 后依次执行：

| # | 命令 | 覆盖用例 |
|---|---|---|
| 1 | `uv run pytest -m needs_funds -v` | test_03 自转×2、test_04 approve、test_05 4337 全链路×2、test_06 ERC-8004 注册、test_07 BDEX swap（共 9） |
| 2 | `python scripts/demo_g5.py` | G5 步骤 8/10/11/13 转为 PASS |
| 3 | （可选）`docker compose up -d` 现场外网可用环境验证容器路径 | — |

## 三、与蓝图的偏差清单（铁律 8：以实测为准，不静默改蓝图）

| # | 偏差 | 原因与依据 |
|---|---|---|
| 1 | G1 归档到 `bot_chain_api/results/` 而非 `docs/bot_chain/` | 本任务禁止改动 `docs/` |
| 2 | `/indexer/subscribe` 实现为 GET + SSE | 03 篇(POST) 与 04 篇(GET SSE) 冲突，取语义更具体的 04 篇 |
| 3 | M4 NFT 路径规范为 `/tokens/erc721/{contract}/owner/{token_id}` 与 `/tokens/erc721/{contract}/token/{token_id}/uri` | 03 篇两处写法不一致，统一补全前缀 |
| 4 | DuckDB 六表均增加 `network` 列 | 04 篇自注明"编码时加列"（主网预拉数据同库隔离） |
| 5 | 新增 `tests/test_00_core.py`、`tests/fakes.py` 与 core/ 下 deps/log/abis/tx/keystore/bundler/explorer/idempotency 八个文件 | D1 覆盖率门 + §B.5 公共逻辑下沉的最小必要扩展（03 篇目录的加细，非冲突） |
| 6 | indexer 全量回补默认不执行，提供 window/from/to 参数 + 水位（含 64 块 reorg 回扫） | 链已 2500 万块，全量回补不现实；04 篇"全量回补"按参数保留 |
| 7 | `service_error`→422（任务规则位置映射），未处理异常→500 | 任务钉死规则与 03 篇"其余→500"并存，按任务规则执行 |
| 8 | 新增 D0 阶段/`d0` tag（规范与约束先行） | 用户指示：规范提前定义、犯错沉淀、每轮静态检查 |
| 9 | **取消自研 /playground 单文件页**：调试用 FastAPI 内置 Swagger UI（`/docs`），`/playground` 302→`/docs`；G5 第 15 步"演示剧本按钮"改为 `scripts/demo_g5.py` | 用户指示（2026-10-01）：playground 用于调试请求而非参赛演示，采用现成 Swagger 类组件 |
| 10 | **IdentityRegistry 实测 `name()`="AgentIdentity"**（`symbol()`="AGENT"） | 02/00 篇写 name=AGENT；实测为准（D2） |
| 11 | **faucet 自动领水不可行**：claim 端点（`api-faucet.bohr.life/botchain/api/v1/faucet/claim`）强制 Cloudflare Turnstile（实测假 token 返 10003）；M10 落地为 status 透传 + token 代发 + 手动指引；02 篇"fixture 自动领水"按其自身兜底路径走手动+skip | D3 探测定案（`results/d3_probes/SUMMARY.md`） |
| 12 | ERC-8004 三合约为 ERC1967Proxy，ABI 经 **EIP-1967 slot 直读 implementation** 后从已验证源码取得（浏览器 v2 contracts 端点 400，v1 getabi 对代理只返回壳） | D3 探测；`getAgentWallet(uint256)` 等视图实测存在，蓝图推测得到证实 |
| 13 | **BOT Chain 为 POA 链**（块 extraData 277B），web3 客户端必须注入 ExtraDataToPOAMiddleware（尽调脚本用裸 JSON-RPC 未暴露） | D1 live 实测（CONSTRAINTS C-04） |
| 14 | Docker 构建在本机因外网镜像源不可达（buildkit resolve 超时）未完成容器验证；G5 按任务预案走本机 uvicorn + 本机 Redis；Dockerfile/compose 已交付（基础镜像 python:3.11-slim，本地有缓存 tag 可离线构建的环境可用） | 本机网络限制；README 已注明双路径 |
| 15 | 主网 faucet_api_url 按测试网同构推定（`api-faucet.botchain.ai`） | 未实测（铁律 7：不主动访问主网域） |

## 四、质量摘要（d5 时点，最终）

```
ruff check .              → All checks passed!（54 文件）
ruff format --check .     → 54 files already formatted
mypy app                  → Success: no issues found in 36 source files（0 error）
pytest -m unit --cov=app  → 103 passed；覆盖率 81.23%（门 ≥80%）
pytest -m live            → 39 passed（测试网 968 只读）
pytest -m needs_funds     → 1 passed / 8 skipped（无私钥显式 skip，附领水指引）
```

静态三连由 `.githooks/pre-commit` 在每次提交机械强制（d0 起全程生效）。

## 五、阶段与 tag

| tag | 提交 | 交付 |
|---|---|---|
| `d0` | chore(init) | uv 工具链、pyproject 钉死配置、CONSTRAINTS v1、pre-commit 钩子 |
| `d1` | test(d1)/feat(d1)/docs(d1) | core 四件套+POA 适配；G1 live 14/14 归档 |
| `d2` | test(d2)/feat(d2)/docs(d2) | M1~M4+M8；unit 67→89、live 19 |
| `d3` | docs(d3) 探测/test(d3)/feat(d3)/docs(d3) | 三项探测归档；M5/M6/M7/M10；unit 89、live 33 |
| `d4` | test(d4)/feat(d4) | storage+M9+幂等；G4（/docs 55 路径实通） |
| `d5` | test(d5)/feat(d5) | Docker/compose/README/demo_g5；G5 十五步 0 FAIL |

## 六、犯错沉淀（CONSTRAINTS §D，共 14 条）

C-01 提交消息必须带 scope ｜ C-02 ruff 中文/测试豁免 ｜ C-03 web3 重试配置显式 errors ｜ C-04 POA 中间件 ｜ C-05 JSON-RPC error 双形态 ｜ C-06 respx 类装饰器吞用例（+S105/S106 token 名豁免） ｜ C-07 生成式资产导入冒烟 ｜ C-08 裸 str 禁入 Web3 hex 转换 ｜ C-09 storage JSON 边界 ANN401 ｜ C-10 unit 隔离/SSE 全流 ｜ C-11 duckdb 主库+WAL 成对清理 ｜ C-12 scripts 豁免 ｜ C-13 dry_run 不依赖链上估值 ｜ C-14 duckdb 跨进程互斥

## 七、复现入口

```bash
cd bot_chain_api && uv sync
uv run uvicorn app.main:app --port 8010     # 调试页 http://localhost:8010/docs
uv run pytest -m "unit or live"             # 142 用例
python scripts/demo_g5.py --base http://localhost:8010
```
