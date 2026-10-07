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
