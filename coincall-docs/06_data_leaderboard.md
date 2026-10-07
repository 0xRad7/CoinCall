# 06 — 数据层：调用流水、收入视图与排行榜

> 前置阅读：00/02（calls 契约）。自包含。

## 1. 职责

把运营事实（calls / receipts / settlements / 钱包绑定）转成两类产出：
① API 视图（账单/排行/商店页数据）；② demo 大屏素材（收入即信誉）。
本层**纯读**，不产生新业务事实；表由各平面写入（calls/receipts←02，settlements←04，钱包绑定←03）。

## 2. 汇总表与视图（DuckDB）

```sql
-- 每日服务级汇总（排行/商店页主源）
CREATE VIEW v_service_daily AS
SELECT
  service_id, provider_agent_id,
  date_trunc('hour', created_at) AS hour,
  count(*) FILTER (WHERE status='success')        AS calls_ok,
  count(*) FILTER (WHERE status='aborted')        AS calls_aborted,
  sum(amount_raw) FILTER (WHERE status='success') AS revenue_raw,
  avg(latency_ms)                                 AS avg_latency_ms
FROM calls GROUP BY 1,2,3;

-- 服务总分视图（排行榜直读）
CREATE VIEW v_service_summary AS
SELECT
  service_id, provider_agent_id,
  count(*) FILTER (WHERE status='success')  AS total_calls,
  sum(amount_raw) FILTER (WHERE status='success') AS total_revenue_raw,
  count(*) FILTER (WHERE status='aborted')  AS total_failures,
  max(created_at) AS last_call_at
FROM calls GROUP BY 1,2;

-- Provider 信用画像（收入是最硬信誉，无人工评价）
CREATE VIEW v_provider_credit AS
SELECT provider_agent_id,
       sum(total_revenue_raw) AS revenue_raw,
       sum(total_calls)       AS calls,
       sum(total_failures)    AS failures,
       round(1.0 * sum(total_failures) / greatest(sum(total_calls),1), 3) AS fail_rate
FROM v_service_summary GROUP BY 1;
```

补充表（已有平面创建，此处声明归属）：`settlements`（04）、`receipts`（02）、`wallet_binds`（03）。

## 3. 本层 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/leaderboard/services` | `?order=revenue|calls`，默认收入降序；字段：service_id、name（join services manifest）、provider、revenue、calls、fail_rate、last_call_at |
| GET | `/leaderboard/providers` | v_provider_credit + display_name |
| GET | `/store` | active 服务 × 汇总指标（人读商店页后端） |
| GET | `/stats/overview` | 平台总览：总调用、总 GMV（success 计费额）、活跃 consumer/provider 数——**demo 大屏/演讲开场数字** |

排序口径固定为**收入优先**（00 铁律：收入=最硬信誉，无评价机制）。

## 4. 链上核对（信任展示，P1）

`GET /leaderboard/providers/{agent_id}/proof`：
- 返回该 provider 的链上可核事实：agentWallet 地址（经 coincall-bot-chain-api identity 视图）+ settlements 链上交易哈希列表；
- 演示话术："每个数字都可以在 scan.bohr.life 查到对应转账"。

## 5. 验收（DoD）

1. unit：构造样本 calls/settlements/receipts 后，三个视图与 API 输出逐字段断言（含 FILTER 语义）；
2. 排行榜在空库下优雅返回空列表（不 500）；
3. overview 的 GMV 与 settlements 流水一致（v2 无平台账本：对账以 04 合约不变量 I4 链上即对账为准）。

## 6. 依赖

- 读：calls/receipts（02）、settlements（04）、钱包绑定（03）、services manifest（01）；
- coincall-bot-chain-api：identity 视图（proof 特性）；无新端点需求。
