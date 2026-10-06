"""调用统计只读端点：GET /internal/stats/calls（P1-3 活跃度数据源，core 排行榜消费）。

口径（06 §3 活跃度侧；收入侧以链上 Charged 为唯一真相，不在此表）：
- calls_success = status IN ('success','settled')——计费成功（settled 是已链上结算的成功）；
- settle_pending 按 call_id 回连 calls.service_id 归属服务（孤儿 settle 行不计数）；
- 只读自有 DuckDB（A4：calls.py 冻结契约不改，本模块经 store.conn 只读查询）。

10 §1/§2 冻结契约（决策层扩展）：window_hours 窗口化（默认 168、上限 720），新增
- p50_ms / p95_ms：窗口内 success/settled 行 latency_ms 的 quantile_cont（取整毫秒）；
- distinct_payers：窗口内 success/settled 行的 COUNT(DISTINCT consumer_wallet)；
- last_activity_at：窗口内最后活动（任何 status 的 max(created_at)）；
- totals 的分位与去重为**池化**口径（同一 SQL 去掉 GROUP BY），不是逐服务相加。
既有字段（counts/settle_pending/last_call_at）语义不变，仅计数范围随窗口收窄；
settle_pending 是队列当前深度，不随窗口化（现势指标）。
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.modules.calls import CallStore

router = APIRouter(prefix="/internal/stats", tags=["stats"])

#: 窗口默认与上限（10 冻结契约：window_hours 默认 168、上限 720）
WINDOW_DEFAULT_HOURS = 168
WINDOW_MAX_HOURS = 720

_AGG_COLUMNS = """
  count(*) FILTER (WHERE status IN ('success','settled'))                    AS calls_success,
  count(*) FILTER (WHERE status = 'settled')                                 AS calls_settled,
  count(*) FILTER (WHERE status = 'aborted')                                  AS calls_aborted,
  count(*) FILTER (WHERE status = 'inflight')                                 AS calls_inflight,
  count(*) FILTER (WHERE status = 'bad_debt')                                 AS bad_debt,
  CAST(round(quantile_cont(latency_ms, 0.50) FILTER (
    WHERE status IN ('success','settled') AND latency_ms IS NOT NULL)) AS BIGINT) AS p50_ms,
  CAST(round(quantile_cont(latency_ms, 0.95) FILTER (
    WHERE status IN ('success','settled') AND latency_ms IS NOT NULL)) AS BIGINT) AS p95_ms,
  count(DISTINCT consumer_wallet) FILTER (WHERE status IN ('success','settled')) AS distinct_payers,
  max(created_at)                                                             AS last_call_at
"""

#: S608 豁免：f-string 只拼装本模块常量 _AGG_COLUMNS（无任何外部输入），值仍走 ? 参数化
_PER_SERVICE_SQL = f"""
SELECT service_id, {_AGG_COLUMNS}
FROM calls
WHERE created_at >= now() - to_hours(?)
GROUP BY service_id ORDER BY service_id
"""  # noqa: S608

#: totals：池化口径（去 GROUP BY）——p50/p95 与 distinct_payers 不能逐服务相加；
#: 前置空串 service_id 占位，与 _PER_SERVICE_SQL 的行布局对齐（r[0]=service_id）
_TOTALS_SQL = f"""
SELECT '' AS service_id, {_AGG_COLUMNS}
FROM calls
WHERE created_at >= now() - to_hours(?)
"""  # noqa: S608

_SETTLE_PENDING_SQL = """
SELECT c.service_id AS service_id, count(*) AS pending
FROM settle_queue q JOIN calls c ON c.call_id = q.call_id
WHERE q.status = 'pending' GROUP BY 1
"""


class CallsStatsRow(BaseModel):
    """每服务聚合行（totals 行的 service_id 为空串）。"""

    service_id: str
    calls_success: int
    calls_settled: int
    calls_aborted: int
    calls_inflight: int
    bad_debt: int
    settle_pending: int
    last_call_at: str | None
    p50_ms: int | None = None
    p95_ms: int | None = None
    distinct_payers: int = 0
    last_activity_at: str | None = None
    window_hours: int = WINDOW_DEFAULT_HOURS


class CallsStatsView(BaseModel):
    services: list[CallsStatsRow]
    totals: CallsStatsRow
    generated_at: str


def _row(service_id: str, r: tuple[Any, ...], pending: int, window_hours: int) -> CallsStatsRow:
    """聚合 SQL 行 → 视图行（布局：0 service_id,1..5 counts,6 p50,7 p95,8 payers,9 last）。

    last_activity_at 与窗口内 last_call_at 同值（10 契约新命名与既有命名并存）。
    """
    last = str(r[9]) if r[9] is not None else None
    return CallsStatsRow(
        service_id=service_id,
        calls_success=int(r[1]),
        calls_settled=int(r[2]),
        calls_aborted=int(r[3]),
        calls_inflight=int(r[4]),
        bad_debt=int(r[5]),
        settle_pending=pending,
        last_call_at=last,
        p50_ms=int(r[6]) if r[6] is not None else None,
        p95_ms=int(r[7]) if r[7] is not None else None,
        distinct_payers=int(r[8]),
        last_activity_at=last,
        window_hours=window_hours,
    )


def _rows(store: CallStore, window_hours: int) -> list[CallsStatsRow]:
    pending_by_service: dict[str, int] = {
        str(r[0]): int(r[1]) for r in store.conn.execute(_SETTLE_PENDING_SQL).fetchall()
    }
    rows: list[CallsStatsRow] = []
    for r in store.conn.execute(_PER_SERVICE_SQL, [window_hours]).fetchall():
        rows.append(_row(str(r[0]), r, pending_by_service.pop(str(r[0]), 0), window_hours))
    # settle 队列有 pending 但 calls 窗口内无该服务行（异常态/窗口外）：以零值行补出，不静默丢
    for service_id, pending in sorted(pending_by_service.items()):
        rows.append(
            CallsStatsRow(
                service_id=service_id,
                calls_success=0,
                calls_settled=0,
                calls_aborted=0,
                calls_inflight=0,
                bad_debt=0,
                settle_pending=pending,
                last_call_at=None,
                p50_ms=None,
                p95_ms=None,
                distinct_payers=0,
                last_activity_at=None,
                window_hours=window_hours,
            )
        )
    return rows


def _totals(store: CallStore, rows: list[CallsStatsRow], window_hours: int) -> CallsStatsRow:
    r = store.conn.execute(_TOTALS_SQL, [window_hours]).fetchone()
    if r is None:  # 空表：聚合 SQL 恒返回一行，此为防御分支
        return CallsStatsRow(
            service_id="",
            calls_success=0,
            calls_settled=0,
            calls_aborted=0,
            calls_inflight=0,
            bad_debt=0,
            settle_pending=0,
            last_call_at=None,
            window_hours=window_hours,
        )
    return _row("", r, sum(row.settle_pending for row in rows), window_hours)


@router.get("/calls", response_model=CallsStatsView)
def calls_stats(
    request: Request,
    window_hours: int = Query(default=WINDOW_DEFAULT_HOURS, ge=1, le=WINDOW_MAX_HOURS),
) -> CallsStatsView:
    """自有 calls/settle_queue 的每服务聚合（窗口计数/分位延迟/去重付款人/最近活动）。"""
    store: CallStore = request.app.state.store
    rows = _rows(store, window_hours)
    return CallsStatsView(
        services=rows,
        totals=_totals(store, rows, window_hours),
        generated_at=datetime.now(UTC).isoformat(),
    )
