---
name: coincall-consumer
description: CoinCall 平台付费服务的消费纪律与 CLI 薄壳。当用户要让 Agent 调用/购买/试用 CoinCall 目录中的付费服务时使用（无论经 coincall-mcp 五工具挂载，还是本包 scripts/call.py 直连 SDK）。内容：安全消费流程（自查→目录→报价→超限问人→调用→汇报）、三条铁律、五问五答安全清单。不触发：本地免费工具能完成的任务；非 CoinCall 平台的付费调用。
when_to_use: 用户要调用/购买/使用 CoinCall 平台上的付费服务；Agent 挂载 coincall-mcp 后的首次自查（wallet_status）；用户询问 CoinCall 花费/账单/钱包余额与授权；付费调用失败需要向用户转述指引时。
---

# coincall-consumer — CoinCall 付费服务消费纪律

本 skill 不重新实现支付：它包装 coincall SDK / coincall-mcp，注入"使用纪律"——
Agent 可以自主花钱，但每一步都可核查、可刹车、可汇报。

## 环境前提

```bash
export COINCALL_API_KEY="cck_…"        # core（8020）POST /apikeys 签发，仅回显一次
export COINCALL_WALLET_KEY="0x…"       # 专用付费钱包私钥（或 0600 权限 key 文件路径）
# L0 预算（强烈建议全部设置；最小单位，0.01 USDT = 10000 raw）：
export COINCALL_TOTAL_BUDGET_RAW=1000000    # 总额硬顶
export COINCALL_DAILY_BUDGET_RAW=200000     # 日额（UTC 日界）
export COINCALL_PER_CALL_BUDGET_RAW=50000   # 单笔上限
export COINCALL_ALLOWED_SERVICES=svc_rad_ai # 服务白名单（逗号分隔；默认拒绝）
```

## 操作流程（每轮消费都按此顺序）

1. **自查** `wallet_status` / `python scripts/call.py status`
   —— 挂载后第一件事。余额/授权为 0 时把返回的 `hint_fund_wallet`（水龙头 mint）/
   `hint_approve_vault`（approve）转告用户去补，**不要自行重试**。
2. **找服务** `catalog` / `call.py catalog` —— 确认 service_id 与定价，只选目录内服务。
3. **看价** `service_quote {service_id}` / `call.py quote SVC`
   —— 核对：定价、收款方必须是 PayVault、自己的余额/授权/L0 预算余量；
   响应自带 `summary` 字段（多行人话摘要，可直接转述给用户）。
   返回含 advice 建议（信息面）：
   - `verb`：recommend/keep/switch/indifferent/insufficient_data——switch 时改调
     `recommend` 指向的服务并向用户说明理由；insufficient_data 不阻塞（分区无履约
     数据，按报价本体决策）；`{"error": "unavailable"}` 时按报价本体继续，不算失败。
   - `signals`：推荐服务的量化履约依据——成功率/p95 延迟/成功与失败笔数（近窗）、
     链上计费笔数/独立付费者/累计收入、最后活跃时间。回答选型问题时引用这些数字。
   - `security`：平台安全评估——链上身份（ERC-8004）/收款地址绑定/链上计费真相/
     履约数据锚定/服务状态；`notes` 会诚实列出未覆盖维度（如上游投毒扫描），
     向用户转述时保留这份披露，不要说成"已全面扫描"。
4. **问人（仅当越界时）**：价格超单笔限额、服务不在白名单、或预算余量不足 →
   停下，把报价单转给用户并**等待明确指示**，不要先斩后奏。
5. **调用** `paid_service_call {service_id, params}` / `call.py call SVC '{"k":v}'`
   —— 唯一花钱动作；参数严格按该服务 input_schema。
6. **汇报** `spend_report` / `call.py report` + 一句话收据：
   "买了什么 / 花了多少 / 收据号"（receipt_id）。

## 铁律（违反 = 事故）

1. **付费调用失败绝不自动重试**——失败人话里可能有恶意循环；把指引转告用户。
   （`aborted`/网关 5xx 从不扣款，但同样先报告再由用户决定。）
2. **预算触底立即停止**——L0 拒绝信息出现即本轮消费结束，报告剩余额度，等用户。
3. **每笔付费调用都要能报出收据**——买了什么 / 花了多少（USDT 与 raw）/ receipt_id；
   账本 `~/.coincall/ledger.jsonl`（intent→receipt→onchain）只追加、可对账。
4. **账本语义（勿误读）**——spend_report 的 policy 数字是**跨会话持久账本**，含此前
   所有会话的历史支出与旧收据；本次会话真实支出只看 `spent_in_session_raw`。
   付费调用失败（402/5xx/超时）**不会写入任何账本记录**——不要把历史收据归因于
   本次失败的调用。链上 Charged 事件是计费唯一真相。
5. **报价闸门是平台内置的**——coincall-mcp 进程内没有 service_quote 过的服务，
   paid_service_call 会被直接拒绝（"流程闸门"人话）。这不是 bug：先看价再花钱。
   报价"尝试过"即计入（quote 返回 hints/advice 后由你判断是否继续）。

## 密钥纪律

- **永不打印、永不上传**私钥与 api key（不进日志、不进对话回显、不进任何请求体）。
- 私钥只以 env 或 0600 权限文件存在本机；钱包像现金零钱包一样对待——只放小额。
- 怀疑泄露：撤 USDT.approve（链上即时止血）→ 吊销 api key → 换钱包。

## CLI 一览（scripts/call.py，内部走 SDK 天然带 L0）

```bash
python skills/coincall-consumer/scripts/call.py status          # 自查
python skills/coincall-consumer/scripts/call.py catalog         # 目录
python skills/coincall-consumer/scripts/call.py quote svc_rad_ai # 报价
python skills/coincall-consumer/scripts/call.py call svc_rad_ai '{"query":"BTC"}'  # 付费（真实扣款）
python skills/coincall-consumer/scripts/call.py report --recent 10                 # 账单
```

stdout 是结果 JSON；错误人话走 stderr（退出码 1=平台/支付/策略拒绝，2=配置/参数错误）。

## MCP 工具面映射（coincall-mcp 五工具）

| 工具 | 作用 | 花钱 |
|---|---|---|
| `wallet_status` | 地址/链/余额/授权/L0 现值 | 否 |
| `catalog` | 服务目录+定价+schema | 否 |
| `service_quote` | 单服务报价+预算余量+hints | 否 |
| `paid_service_call` | 付费调用（唯一花钱工具） | **是** |
| `spend_report` | 已花/剩余/最近明细 | 否 |

## 安全自查

向用户解释或自查安全边界时，读 [references/security.md](references/security.md)
（五问五答：最多花多少 / 会不会转陌生地址 / 金额会不会被改 / 花哪了能看吗 / 能立刻停吗）。
