# AGENTS.md — coincall-sdk 子智能体入口

## 服务身份

- **coincall-sdk**：CoinCall 消费面 Python SDK（导入名 `coincall`）——本地付费钱包 +
  EIP-712 签名支付 + MCP 工具封装，"5 分钟接入"的承诺物。
- 对接：core（8020：POST /apikeys、GET /catalog）、gateway（8030：POST /call/{service_id}）、
  测试网 968（rpc.bohr.life，MockUSDT 6 位精度，gas 恒 20 gwei）。
- 冻结契约点：`coincall/signing.py`（EIP-712 digest 独立实现，T17 黄金向量锁死，发现问题上报不绕过）。

## 全量测试入口（本仓门禁）

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy coincall tools  # 静态三连
uv run pytest -q -m unit --cov=coincall --cov-fail-under=80                        # 单测 + 覆盖率 ≥80%
uv run pytest -q -m live                                                           # 只读 live 冒烟
uv run pytest -q -m needs_funds                                                    # 真实付费端到端（需服务在跑）
```

## 绑定校验入口（制品链对账）

- 制品链宿主：`../coincall-core/`（对本任务**只读**）。
- 绑定索引：`../coincall-core/tests/TEST-20261005221052-coincall-p1.md`（P1）。
- 本仓承担的可执行用例绑定：**T16**（`tests/test_wallet.py`）/ **T17**（`tests/test_signing.py::test_golden_vector`，
  消费 `../coincall-contracts/vectors/eip712_golden.json`，文件不在场即 skip）/
  **T18**（`tests/test_client.py`，mock 网关三路）/ **T19**（`tests/test_mcp.py`）。
- 规范正文：`../coincall-docs/03_consumer_sdk.md`（v2）与 `09_p0_p1_tasks.md` P1-1 节（只读）。

## 使用（见 README《5 分钟接入》）

```bash
uv sync
uv run python -c "import coincall; print(coincall.__version__)"
COINCALL_API_KEY=… COINCALL_WALLET_KEY=0x… uv run python tools/mcp_server.py   # MCP stdio
```

## 工程纪律

见 `CONSTRAINTS.md`（静态三连 / 分型提交 / 私钥不出机器 / 零网络单测 / trust_env=False / 偏差记录区）。
