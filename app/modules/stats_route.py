"""调用统计只读端点：GET /internal/stats/calls（P1-3 活跃度数据源，core 排行榜消费）。

口径（06 §3 活跃度侧；收入侧以链上 Charged 为唯一真相，不在此表）：
- calls_success = status IN ('success','settled')——计费成功（settled 是已链上结算的成功）；
- settle_pending 按 call_id 回连 calls.service_id 归属服务（孤儿 settle 行不计数）；
- 只读自有 DuckDB（A4：calls.py 冻结契约不改，本模块经 store.conn 只读查询）。
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.modules.calls import CallStore

router = APIRouter(prefix="/internal/stats", tags=["stats"])

_PER_SERVICE_SQL = """
SELECT
  service_id,
  count(*) FILTER (WHERE status IN ('success','settled')) AS calls_success,
  count(*) FILTER (WHERE status = 'settled')             AS calls_settled,
  count(*) FILTER (WHERE status = 'aborted')             AS calls_aborted,
  count(*) FILTER (WHERE status = 'inflight')            AS calls_inflight,
  count(*) FILTER (WHERE status = 'bad_debt')            AS bad_debt,
  max(created_at)                                        AS last_call_at
FROM calls GROUP BY service_id ORDER BY service_id
"""

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


class CallsStatsView(BaseModel):
    services: list[CallsStatsRow]
    totals: CallsStatsRow
    generated_at: str


def _rows(store: CallStore) -> list[CallsStatsRow]:
    pending_by_service: dict[str, int] = {
        str(r[0]): int(r[1]) for r in store.conn.execute(_SETTLE_PENDING_SQL).fetchall()
    }
    rows: list[CallsStatsRow] = []
    for r in store.conn.execute(_PER_SERVICE_SQL).fetchall():
        rows.append(
            CallsStatsRow(
                service_id=str(r[0]),
                calls_success=int(r[1]),
                calls_settled=int(r[2]),
                calls_aborted=int(r[3]),
                calls_inflight=int(r[4]),
                bad_debt=int(r[5]),
                settle_pending=pending_by_service.pop(str(r[0]), 0),
                last_call_at=str(r[6]) if r[6] is not None else None,
            )
        )
    # settle 队列有 pending 但 calls 无该服务行（异常态）：以零值行补出，不静默丢
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
            )
        )
    return rows


def _totals(rows: list[CallsStatsRow]) -> CallsStatsRow:
    def total(field: str) -> int:
        return sum(int(getattr(row, field)) for row in rows)

    last: Any = None
    for row in rows:
        if row.last_call_at and (last is None or row.last_call_at > last):
            last = row.last_call_at
    return CallsStatsRow(
        service_id="",
        calls_success=total("calls_success"),
        calls_settled=total("calls_settled"),
        calls_aborted=total("calls_aborted"),
        calls_inflight=total("calls_inflight"),
        bad_debt=total("bad_debt"),
        settle_pending=total("settle_pending"),
        last_call_at=last,
    )


@router.get("/calls", response_model=CallsStatsView)
def calls_stats(request: Request) -> CallsStatsView:
    """自有 calls/settle_queue 的每服务聚合（success/aborted/pending/坏账/最近调用）。"""
    store: CallStore = request.app.state.store
    rows = _rows(store)
    return CallsStatsView(
        services=rows,
        totals=_totals(rows),
        generated_at=datetime.now(UTC).isoformat(),
    )
