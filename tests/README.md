# tests/ — 绑定明细（TEST-20261005221052 CoinCall P1，本仓 T16~T19）

> 绑定索引：`../coincall-core/tests/TEST-20261005221052-coincall-p1.md`；本文件落本仓用例明细。
> 跑法：`uv run pytest -q -m unit --cov=coincall --cov-fail-under=80`（门禁，零网络）；
> live / needs_funds 需 core(8020)+gateway(8030) 在跑（连接拒绝=兄弟任务重启服务，用例内置 5s×N 重试）。

| # | 规范条目 | 可执行用例（标识） | 级别 | 状态 |
|---|---|---|---|---|
| T16 | 03 §3 本地钱包生成/导入，私钥不出机器 | `test_wallet.py`（15 例：create/from_key 三源/0600 拒 0644/approve 直签 calldata 逐字节/错链拒签/balance 口径/mint/签名恢复） | unit | 绿（P1-1） |
| T17 | 03 §7 SDK digest 与 P0 冻结口径一致 | `test_signing.py::test_golden_vector`（3 向量 domainSeparator/structHash/digest 逐字节 + recover==address）+ 确定性签名 + 头别名 + 域绑定（4 例） | golden | 绿（P1-1；向量 `../coincall-contracts/vectors/eip712_golden.json`，不在场即 skip） |
| T18 | 03 §7 SDK 三路 success / 402 带指引 / aborted 从未扣款 | `test_client.py`（10 例：MockTransport 三路 + X-PAYMENT 独立恢复验证 + 预算拒发请求 + ETag 304 + 无钱包降级） | unit | 绿（P1-1） |
| T19 | 03 §6 MCP catalog / paid_service_call 两工具 | `test_mcp.py`（11 例：握手/列工具/两工具成功/402→isError 人话/-32601/-32602/通知不回帧/serve 循环/env 装配/子进程 stdio 冒烟） | unit | 绿（P1-1） |
| （附加） | 03 §7-2 资金准备全链路 + 04 keeper 结算 | `test_live_smoke.py::test_paid_call_e2e_settled_by_keeper`（anvil#1 → mint/approve 本地直签 → core 签 key → svc_e2e_demo 付费 200 → keeper ≤90s Charged + 余额减 10000） | needs_funds | 绿（P1-1 实跑，keeper tx 见 03 幕墙） |
| （附加） | 目录可被 SDK catalog 消费 | `test_live_smoke.py::test_catalog_live`（真 core 8020 只读） | live | 绿（P1-1） |

## 实测契约注记（写进用例的对接事实）

- 网关 402 质询实测带 `payment.approve_to == 0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471`
  与 `pricing.amount_raw`（gateway `build_challenge` 口径），SDK 的 `PaymentRequiredError.guidance`
  以这两个字段生成 approve/转入指引——字段改名会破坏人话指引，属契约级变更。
- anvil#1（`0x7099…79C8`）在测试网 968 持有 BOT 可付 gas；MockUSDT `mint` selector `0x40c10f19`、
  `approve` `0x095ea7b3`、`balanceOf` `0x70a08231`、`allowance` `0xdd62ed3e`（手工编码，无 ABI 依赖）。
- 网关幂等：`X-Idempotency-Key` 同键同体重放、同键异体 409——SDK 自动幂等键 = sha256(service_id + 规范化参数)，
  重复同参调用会被重放（不重复扣款）。
