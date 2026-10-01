"""Redis 运行时协调（04 篇键空间）：缓存/nonce 锁/幂等/Pub-Sub。

全部可丢弃可重建；REDIS_URL 未配置时上层降级（进程锁/内存幂等）。
"""

import json
from typing import Any

import redis

NONCE_LOCK_TTL_S = 30
IDEM_TTL_S = 300


class RedisStore:
    def __init__(self, client: "redis.Redis") -> None:
        self._r = client

    # ---- cache:chain / cache:token / cache:bdex ----------------------------
    def cache_get(self, key: str) -> Any:
        raw = self._r.get(f"cache:{key}")
        return json.loads(raw) if raw else None

    def cache_set(self, key: str, value: Any, ttl: int) -> None:
        self._r.set(f"cache:{key}", json.dumps(value, ensure_ascii=False, default=str), ex=ttl)

    # ---- lock:nonce（SETNX + TTL；防双花 nonce）-----------------------------
    def acquire_nonce_lock(self, address: str, ttl: int = NONCE_LOCK_TTL_S) -> bool:
        return bool(self._r.set(f"lock:nonce:{address}", "1", nx=True, ex=ttl))

    def release_nonce_lock(self, address: str) -> None:
        self._r.delete(f"lock:nonce:{address}")

    # ---- idem（写接口幂等：同 key 5 分钟防重放）-----------------------------
    def idem_put(self, key: str, response: Any, ttl: int = IDEM_TTL_S) -> bool:
        """首次写入返回 True；已存在（重放）返回 False。"""
        ok = self._r.set(
            f"idem:{key}", json.dumps(response, ensure_ascii=False, default=str), nx=True, ex=ttl
        )
        return bool(ok)

    def idem_get(self, key: str) -> Any:
        raw = self._r.get(f"idem:{key}")
        return json.loads(raw) if raw else None

    # ---- ch:blocks / ch:events（新块/新事件广播）---------------------------
    def publish(self, channel: str, payload: Any) -> None:
        self._r.publish(channel, json.dumps(payload, ensure_ascii=False, default=str))

    def pubsub(self) -> Any:  # redis PubSub 动态类型
        return self._r.pubsub()
