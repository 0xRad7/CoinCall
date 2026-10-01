# bot_chain_api

BOT Chain 全功能链上服务（ETH Wuhan 2026 · 赛题接入后端）。赛时 Agent / 前端只对接本服务的
REST API（`/api/v1`，默认测试网 Chain ID **968**），不再现场写链代码。

- 研发规范（铁律/工程规范/静态检查门/犯错沉淀）：[CONSTRAINTS.md](CONSTRAINTS.md)
- 验收与偏差：[RESULTS.md](RESULTS.md)
- 调试页：**`/docs`（FastAPI 内置 Swagger UI，Try-it-out 即请求调试器，离线可用）**；
  `/playground` 302 跳转 `/docs`；`/redoc` 只读文档

## 快速启动

### 方式一：Docker Compose（推荐，api + redis）

```bash
cp .env.example .env          # 按需填写（见下表）；领水后把测试网私钥填入
docker compose up -d          # 构建并启动，等待 ~10s
curl http://localhost:8000/api/v1/chain/health   # 三通道自检
python scripts/demo_g5.py     # G5 十五步 Demo（无资金时写链步降级 dry_run+NEEDS_FUNDS）
```

### 方式二：本机 uvicorn（无 Docker 兜底）

```bash
uv sync
uv run uvicorn app.main:app --port 8000
# 可选 Redis：redis-server &  且 .env 设置 REDIS_URL=redis://localhost:6379/0
```

## 领水指引（重要：无法全自动）

测试网水龙头 `https://faucet.bohr.life/basic` 强制 **Cloudflare Turnstile 人机验证**
（实测错误码 10003，见 `results/d3_probes/SUMMARY.md`），因此服务无法自动领水：

1. 浏览器打开 <https://faucet.bohr.life/basic>，粘贴你的测试网地址，完成验证领取
   （每次 **10 BOT + 1000 tUSDT**，冷却约 10 分钟，可重复）。
2. 把该地址的私钥写入 `.env`：`BOT_CHAIN_TEST_PRIVATE_KEY=0x…`（**仅允许测试网私钥**），
   `docker compose up -d` 重启后生效。
3. 补跑写链用例与 Demo：`uv run pytest -m needs_funds` 与 `python scripts/demo_g5.py`。

也支持半自动：从浏览器开发者工具复制 `turnstileToken` 后
`POST /api/v1/faucet/claim {"address":…, "turnstile_token":…, "dry_run":false}` 代发。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `BOT_CHAIN_NETWORK` | `testnet` | `testnet`(968) / `mainnet`(677，还需双重锁) |
| `BOT_CHAIN_ALLOW_MAINNET` | 空 | 设 `1` 才允许主网（默认禁用） |
| `BOT_CHAIN_TEST_PRIVATE_KEY` | 空 | 测试网出资私钥（仅测试网；接口/日志永不回显） |
| `BOT_CHAIN_KEYSTORE_SECRET` | 空 | 代管账户 keystore 加密口令（未设=进程内临时态，重启丢失） |
| `REDIS_URL` | 空 | 如 `redis://localhost:6379/0`；未设自动降级进程锁/内存幂等 |
| `DUCKDB_PATH` | `data/botchain.duckdb` | 分析库路径 |
| `BOT_CHAIN_API_KEY` | 空 | 设置后 `/api` 需带 `X-API-Key` |
| `PROXY` | 空 | 仅 `botchain.ai` 主网域需要；`*.bohr.life` 永远直连 |

## API 速览（55 端点，`/docs` 交互式文档）

| 模块 | 前缀 | 要点 |
|---|---|---|
| M1 链信息 | `/api/v1/chain` | info/gas(恒 20gwei)/stats/blocks/health |
| M2 账户 | `/api/v1/accounts` | 生成 EOA（keystore 代管）/balances/nonce/历史 |
| M3 交易 | `/api/v1/tx` | transfer（dry_run 默认）/send-raw/回执/事件解码 |
| M4 代币 | `/api/v1/tokens` | ERC20/721 读写（dry_run 默认） |
| M5 账户抽象 | `/api/v1/aa` | 4337 EntryPoint v0.7 + Bundler 全链路 |
| M6 Agent 身份 | `/api/v1/agent-identity` | ERC-8004 register/聚合视图/信誉/验证 |
| M7 DEX | `/api/v1/bdex` | V2 报价/兑换（dry_run 默认） |
| M8 通用合约 | `/api/v1/contracts` | 任意 call/send/deploy/decode |
| M9 索引 | `/api/v1/indexer` | 4 类 sync + status + SSE subscribe |
| M10 水龙头 | `/api/v1/faucet` | status（/info 透传）/claim（token 代发或手动指引） |

**写接口安全默认**：一律 `dry_run=true` 返回未签名预览；真实发送必须显式 `dry_run=false`。

## 开发与质量门

```bash
ruff check . && ruff format --check . && mypy app     # 每轮静态三连（pre-commit 强制）
pytest -m unit -q --cov=app --cov-fail-under=80        # 覆盖率门
pytest -m live                                         # 测试网只读
pytest -m needs_funds                                  # 写链（需 .env 私钥，否则显式 skip）
```

## 目录速查

```
app/core/     配置/链常量(地址唯一来源)/RPC/错误/日志/签名/keystore/4337/幂等
app/modules/  M1~M10 业务路由
app/storage/  DuckDB(6 表)/Redis(键空间)
scripts/      demo_g5.py 十五步剧本
results/      G1 归档/D3 探测/G5 记录
CONSTRAINTS.md  研发规范与犯错沉淀（活文档）
```
