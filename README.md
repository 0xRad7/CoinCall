# CoinCall Core（主仓库）

**CoinCall —— BOT Chain 上的 x402 式按次付费 Agent 服务层**：任何 Agent 能力 30 秒变成收费服务，消费端 Agent 用本地钱包按次付费调用；失败=从未扣款，资金永不过平台手。本仓承载管理面服务（manifest 目录 / api key / Provider 登记 / 排行榜）与项目级制品链（intents/ specs/ plans/ tests/，无远端，评审以本地自查替代）。

## 仓库族（同级目录）

| 目录 | 角色 | 端口 | 关键 tag |
|---|---|---|---|
| `coincall-core` | 管理面：manifests/catalog、apikey 生命周期、providers 登记（链上身份校验）、leaderboard（收入=链上 Charged 唯一真相） | 8020 | `p0`、`p1` |
| `coincall-gateway` | 数据面：`POST /call/{service_id}` 7 步时序、402 质询、影子闸门、keeper 结算器（默认 3 笔/30s 批量上链）、内置 demo 服务、演示脚本 | 8030 | `d0`、`p0-e2e` |
| `coincall-bot-chain-api` | 链适配：BOT Chain 全量链上操作（ERC-8004 身份/钱包绑定/4337/合约读写/logs 透传） | 8010 | `d7-api-ext` |
| `coincall-contracts` | 合约仓：PayVault（EIP-3009 语义批量扣款 + credits + 提现恒开）、MockUSDT、EIP-712 黄金向量、部署事实 | — | `d0-contracts-r1` |
| `coincall-sdk` | 消费端：本地付费钱包 / `Client.call()` 签名付费 / MCP server 两工具（catalog、paid_service_call） | — | `d0` |
| `coincall-docs` | 规范文档族 00~09 + 演示片（`deck/deck.html` 产品叙事版、`deck/deck_v1_internal.html` 立项叙事版） | — | — |

链上事实唯一来源：`coincall-contracts/deployments/testnet-968.json`（PayVault / MockUSDT / operator 地址与部署哈希）。

## 运行 runbook（赛时铁律）

1. **服务必须从主会话拉起**——子智能体/子进程会话结束会被回收（两轮实证）；
2. 8030 必须带 `COINCALL_KEEPER_ENABLED=true`，否则结算不跑；
3. DuckDB 单写者（C-14）：跑某仓测试前先停对应服务（`lsof -ti :PORT | xargs kill`），测完重启；
4. 拉起命令（路径含 `&` 需转义）：
   ```zsh
   cd ~/Documents/S1\&ETHwuhan/coincall-bot-chain-api && nohup uv run uvicorn app.main:app --port 8010 >/tmp/bca.log 2>&1 &
   cd ~/Documents/S1\&ETHwuhan/coincall-core            && nohup uv run uvicorn app.main:app --port 8020 >/tmp/core.log 2>&1 &
   cd ~/Documents/S1\&ETHwuhan/coincall-gateway          && COINCALL_KEEPER_ENABLED=true nohup uv run uvicorn app.main:app --port 8030 >/tmp/gw.log 2>&1 &
   ```
5. 三幕演示（无人工干预，彩排实测 5 分 58 秒）：`cd coincall-gateway && uv run python scripts/demo_p1.py`，实录与交易哈希见其 `results/demo_p1.md`；单笔端到端冒烟：`scripts/e2e_p0.py`；
6. 各仓质量门：`uv run ruff check . && uv run ruff format --check . && uv run mypy app` + `uv run pytest -m unit`；**改过 pyproject 后首跑 ruff 加 `--no-cache`**（C-08 教训）。

## 验收与过程留痕

- **P0**（身份端点 / PayVault / 骨架+冻结契约 / 402 网关 / keeper）：`results/P0-ACCEPTANCE.md`——五项全过
- **P1**（SDK / Provider 接入层 / 排行榜 / 演示集成）：`results/P1-ACCEPTANCE.md`——四项全过
- 制品链：`intents/ → specs/ → plans/ → tests/`（P0=`20261005202947`，P1=`20261005221052`；发起人授权自动验收，过程全留痕）
- 各仓 `CONSTRAINTS.md` §C/§D/§E：工程纪律、犯错沉淀（C-06 工具链版本漂移 / C-08 ruff 缓存掩盖 / C-14 DuckDB 单写者…）与对规范文档的实测偏差

## 全量测试入口

见 `AGENTS.md`；本仓用例绑定明细见 `tests/README.md`。

## Provider 接入

五步上架即售（注册身份→绑钱包→登记→发布→上榜）：见 `PROVIDER_ONBOARDING.md`。
