"""影子闸门（02 §5c）：available = min(链上) − Σ在途；K 并发上限；fail-closed。

性能层非资金层：拒绝即 402，绝不放行不确定的请求。
"""

import threading
from dataclasses import dataclass


@dataclass
class GateDecision:
    allowed: bool
    code: str | None = None
    detail: str = ""


class ShadowGate:
    """每 api key 维护在途笔数与在途金额（内存实现，Redis 镜像为 P2）。"""

    def __init__(self, k: int = 3) -> None:
        self.k = k
        self._lock = threading.Lock()
        self._inflight: dict[str, list[int]] = {}

    def try_acquire(self, key_id: str, amount_raw: int, *, limit: int | None) -> GateDecision:
        """limit = min(链上 balance, allowance)；None 表示链上未知 → fail-closed。"""
        with self._lock:
            if limit is None:
                return GateDecision(False, "shadow_fail_closed", "链上额度未知，拒绝放行")
            inflight = self._inflight.get(key_id, [])
            if len(inflight) >= self.k:
                return GateDecision(False, "shadow_k_exceeded", f"在途笔数已达上限 K={self.k}")
            if sum(inflight) + amount_raw > limit:
                return GateDecision(
                    False,
                    "shadow_amount_exceeded",
                    f"在途 {sum(inflight)} + 本笔 {amount_raw} 超过链上额度 {limit}",
                )
            inflight.append(amount_raw)
            self._inflight[key_id] = inflight
            return GateDecision(True)

    def release(self, key_id: str, amount_raw: int) -> None:
        with self._lock:
            inflight = self._inflight.get(key_id, [])
            if amount_raw in inflight:
                inflight.remove(amount_raw)
            self._inflight[key_id] = inflight

    def snapshot(self, key_id: str) -> tuple[int, int]:
        """(在途笔数, 在途金额)——诊断与测试用。"""
        with self._lock:
            inflight = self._inflight.get(key_id, [])
            return len(inflight), sum(inflight)
