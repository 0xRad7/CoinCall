"""T12：02 节影子闸门——available = min(链上) − Σ在途；K=3 并发上限；fail-closed。"""

import pytest

from app.core.shadow_gate import ShadowGate

pytestmark = pytest.mark.unit


def test_acquire_up_to_k_then_reject() -> None:
    gate = ShadowGate(k=3)
    for i in range(3):  # 同一 key 连续三笔在途
        decision = gate.try_acquire("key_1", 100, limit=10_000)
        assert decision.allowed, f"第 {i + 1} 笔应放行"
    fourth = gate.try_acquire("key_1", 100, limit=10_000)
    assert not fourth.allowed
    assert fourth.code == "shadow_k_exceeded"


def test_amount_ceiling_inflight_sum() -> None:
    gate = ShadowGate(k=3)
    assert gate.try_acquire("k", 6_000, limit=10_000).allowed
    second = gate.try_acquire("k", 5_000, limit=10_000)  # 6000+5000 > 10000
    assert not second.allowed
    assert second.code == "shadow_amount_exceeded"


def test_fail_closed_without_chain_limit() -> None:
    gate = ShadowGate(k=3)
    decision = gate.try_acquire("k", 100, limit=None)
    assert not decision.allowed
    assert decision.code == "shadow_fail_closed"


def test_release_frees_slot_and_amount() -> None:
    gate = ShadowGate(k=1)
    assert gate.try_acquire("k", 6_000, limit=10_000).allowed
    assert not gate.try_acquire("k", 1, limit=10_000).allowed  # 同 key 第二笔撞 K=1
    gate.release("k", 6_000)
    assert gate.try_acquire("k", 10_000, limit=10_000).allowed


def test_snapshot_tracks_inflight() -> None:
    gate = ShadowGate(k=3)
    gate.try_acquire("k", 100, limit=10_000)
    gate.try_acquire("k", 200, limit=10_000)
    count, total = gate.snapshot("k")
    assert count == 2
    assert total == 300
    assert gate.snapshot("other") == (0, 0)


def test_fifty_sequential_never_exceed_k() -> None:
    """02 §7-3：同 consumer 50 并发调用无超扣（顺序模拟口径）。"""
    gate = ShadowGate(k=3)
    acquired = 0
    for _i in range(50):
        if gate.try_acquire("k", 200, limit=10_000).allowed:
            acquired += 1
        assert gate.snapshot("k")[0] <= 3
        assert gate.snapshot("k")[1] <= 10_000
    assert acquired == 3  # 无 release 时恰好 K 笔
