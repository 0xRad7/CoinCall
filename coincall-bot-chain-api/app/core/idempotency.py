"""写接口幂等（Idempotency-Key 头，5 分钟防重放）：Redis 或内存降级。"""

import hashlib
import json
import threading
import time
from typing import Any

from app.storage.redis_store import RedisStore

IDEM_TTL_S = 300
MEMORY_MAX_KEYS = 10_000


class IdempotencyStore:
    """check_and_reserve(key, body) → True=首次放行；False=重放拒绝。"""

    def __init__(self, redis: RedisStore | None) -> None:
        self._redis = redis
        self._memory: dict[str, tuple[str, float]] = {}
        self._guard = threading.Lock()

    def check_and_reserve(self, key: str, body: Any) -> bool:  # noqa: ANN401  # 任意 JSON body
        digest = _body_digest(body)
        if self._redis is not None:
            # set-if-absent：键含 body 摘要，同 key 不同 body 亦拒绝
            return self._redis.idem_put(f"{key}:{digest}", {"status": "inflight"}, IDEM_TTL_S)
        now = time.time()
        with self._guard:
            self._evict(now)
            existing = self._memory.get(key)
            if existing is not None:
                return False  # 同 key（无论 body）拒绝
            self._memory[key] = (digest, now + IDEM_TTL_S)
            return True

    def _evict(self, now: float) -> None:
        if len(self._memory) <= MEMORY_MAX_KEYS:
            expired = [k for k, (_, exp) in self._memory.items() if exp < now]
        else:
            expired = sorted(self._memory, key=lambda k: self._memory[k][1])[:1000]
        for k in expired:
            self._memory.pop(k, None)


def _body_digest(body: Any) -> str:  # noqa: ANN401  # 任意 JSON body
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]
