"""L0 策略引擎（agent-wallet-trust.md §3）：三重硬预算(落盘)/白名单(默认拒绝)/速率/账本/熔断。"""

import json

import pytest

from coincall.policy import PolicyConfig, PolicyEngine, PolicyViolationError

pytestmark = pytest.mark.unit


def cfg(**kw):
    base: dict = {
        "total_budget_raw": 1000,
        "daily_budget_raw": 500,
        "max_per_call_raw": 100,
        "allowed_service_ids": None,
        "min_interval_s": 0,
        "max_calls_per_hour": None,
        "state_path": None,
        "ledger_path": None,
    }
    base.update(kw)
    return PolicyConfig(**base)


def test_triple_budget(tmp_path):
    e = PolicyEngine(cfg(state_path=tmp_path / "s.json", ledger_path=tmp_path / "l.jsonl"))
    e.check("svc", 100)  # ok
    with pytest.raises(PolicyViolationError, match="单笔"):
        e.check("svc", 101)
    e.check("svc", 100)
    e.check("svc", 100)
    e.check("svc", 100)
    e.check("svc", 100)  # 日 500 满额
    with pytest.raises(PolicyViolationError, match="日"):
        e.check("svc", 50)
    # 总额边界
    e2 = PolicyEngine(
        cfg(
            total_budget_raw=250,
            daily_budget_raw=1000,
            state_path=tmp_path / "s2.json",
            ledger_path=tmp_path / "l2.jsonl",
        )
    )
    e2.check("svc", 100)
    e2.check("svc", 100)  # 总额 200
    with pytest.raises(PolicyViolationError, match="总额"):
        e2.check("svc", 100)


def test_budget_persists_across_restart(tmp_path):
    p = tmp_path / "s.json"
    e = PolicyEngine(cfg(max_per_call_raw=500, state_path=p, ledger_path=tmp_path / "l.jsonl"))
    e.check("svc", 300)
    e = PolicyEngine(
        cfg(max_per_call_raw=500, state_path=p, ledger_path=tmp_path / "l.jsonl")
    )  # 重启
    with pytest.raises(PolicyViolationError, match="日"):
        e.check("svc", 300)  # 日额 500，已花 300 再花 300 超


def test_allowlist_default_deny(tmp_path):
    e = PolicyEngine(
        cfg(
            allowed_service_ids=["svc_a"],
            state_path=tmp_path / "s.json",
            ledger_path=tmp_path / "l.jsonl",
        )
    )
    e.check("svc_a", 10)
    with pytest.raises(PolicyViolationError, match="白名单"):
        e.check("svc_b", 10)


def test_rate_limit_and_min_interval(tmp_path):
    e = PolicyEngine(
        cfg(min_interval_s=60, state_path=tmp_path / "s.json", ledger_path=tmp_path / "l.jsonl")
    )
    e.check("svc", 10)
    with pytest.raises(PolicyViolationError, match="间隔"):
        e.check("svc", 10)
    e2 = PolicyEngine(
        cfg(
            max_calls_per_hour=2, state_path=tmp_path / "s2.json", ledger_path=tmp_path / "l2.jsonl"
        )
    )
    e2.check("svc", 10)
    e2.check("svc", 10)
    with pytest.raises(PolicyViolationError, match="速率"):
        e2.check("svc", 10)


def test_ledger_append_only_three_stages(tmp_path):
    lp = tmp_path / "l.jsonl"
    e = PolicyEngine(cfg(state_path=tmp_path / "s.json", ledger_path=lp))
    e.check("svc", 10)
    rid = e.record_intent("svc", 10, auth_nonce="0x" + "ab" * 32)
    e.record_receipt(rid, "rcp_1", charged_raw=10)
    e.record_onchain(rid, tx_hash="0xdead", charged_raw=10)
    rows = [json.loads(x) for x in lp.read_text().splitlines()]
    assert len(rows) == 3
    assert rows[0]["stage"] == "intent" and rows[2]["stage"] == "onchain"
    assert rows[2]["receipt_id"] == rid and rows[2]["tx_hash"] == "0xdead"


def test_disable_killswitch(tmp_path):
    e = PolicyEngine(cfg(state_path=tmp_path / "s.json", ledger_path=tmp_path / "l.jsonl"))
    e.disable("手动停机")
    with pytest.raises(PolicyViolationError, match="停机"):
        e.check("svc", 1)


# -- summary()/Ledger.recent()：MCP spend_report/wallet_status/service_quote 的聚合读口 --


def test_summary_budget_view(tmp_path):
    e = PolicyEngine(
        cfg(
            total_budget_raw=1000,
            daily_budget_raw=500,
            max_per_call_raw=100,
            state_path=tmp_path / "s.json",
            ledger_path=tmp_path / "l.jsonl",
        )
    )
    e.check("svc_a", 100)
    s = e.summary()
    assert s["total_budget_raw"] == 1000
    assert s["total_spent_raw"] == 100 and s["total_left_raw"] == 900
    assert s["daily_budget_raw"] == 500
    assert s["daily_spent_raw"] == 100 and s["daily_left_raw"] == 400
    assert s["max_per_call_raw"] == 100
    assert s["disabled_reason"] is None
    e.disable("手动停机")
    assert e.summary()["disabled_reason"] == "手动停机"
    # 未设限的档位显示 None（引擎在位但不设顶）
    e3 = PolicyEngine(
        cfg(
            total_budget_raw=None,
            daily_budget_raw=None,
            state_path=tmp_path / "s3.json",
            ledger_path=tmp_path / "l3.jsonl",
        )
    )
    s3 = e3.summary()
    assert s3["total_budget_raw"] is None and s3["total_left_raw"] is None
    assert s3["daily_left_raw"] is None


def test_summary_rolls_stale_day_for_display(tmp_path):
    """跨 UTC 日后 summary 的日额口径与下一次 check() 一致（显示层先行，不落盘）。"""
    import time as _time

    p = tmp_path / "s.json"
    p.write_text(
        json.dumps(
            {
                "total_spent_raw": 300,
                "day_key": "2000-01-01",  # 早已过期
                "daily_spent_raw": 300,
                "last_call_ts": 0.0,
                "hour_key": "2000-01-01T00",
                "hourly_calls": 1,
                "disabled_reason": None,
                "extra": {},
            }
        )
    )
    e = PolicyEngine(
        cfg(
            total_budget_raw=None,
            daily_budget_raw=500,
            state_path=p,
            ledger_path=tmp_path / "l.jsonl",
        )
    )
    s = e.summary()
    assert s["day_key"] == _time.strftime("%Y-%m-%d", _time.gmtime())
    assert s["daily_spent_raw"] == 0 and s["daily_left_raw"] == 500  # 新日未花
    assert s["total_spent_raw"] == 300 and s["total_left_raw"] is None  # 总额未设限


def test_ledger_recent_last_n_newest_first(tmp_path):
    lp = tmp_path / "l.jsonl"
    e = PolicyEngine(cfg(state_path=tmp_path / "s.json", ledger_path=lp))
    for i in range(3):
        rid = e.record_intent(f"svc_{i}", 10 * (i + 1), auth_nonce="0x" + "ab" * 32)
        e.record_receipt(rid, f"rcp_{i}", charged_raw=10 * (i + 1))
    rows = e.ledger.recent(3)
    assert len(rows) == 3
    # 文件序 intent0,receipt0,intent1,receipt1,intent2,receipt2 → 倒序截 3：
    assert rows[0]["stage"] == "receipt" and rows[0]["gateway_receipt"] == "rcp_2"  # 最新在前
    assert rows[1]["stage"] == "intent" and rows[1]["service_id"] == "svc_2"
    assert rows[-1]["stage"] == "receipt" and rows[-1]["gateway_receipt"] == "rcp_1"
    assert [r["charged_raw"] for r in rows if r["stage"] == "receipt"] == [30, 20]
    assert e.ledger.recent(2)[0] == rows[0]  # 截断仍从最新起
    assert len(e.ledger.recent(99)) == 6  # 不超过实际行数
    # 空账本 → 空表
    e2 = PolicyEngine(cfg(state_path=tmp_path / "s2.json", ledger_path=tmp_path / "l2.jsonl"))
    assert e2.ledger.recent(5) == []


def test_ledger_recent_skips_malformed_lines(tmp_path):
    """只追加文件可能有崩溃残留半行：recent() 跳过坏行不炸（审计读口必须稳）。"""
    lp = tmp_path / "l.jsonl"
    lp.write_text(
        '{"stage": "intent", "receipt_id": "pl_1"}\n{"stage": "receipt", "receipt_i'  # 半行
    )
    e = PolicyEngine(cfg(state_path=tmp_path / "s.json", ledger_path=lp))
    rows = e.ledger.recent(10)
    assert len(rows) == 1 and rows[0]["receipt_id"] == "pl_1"
