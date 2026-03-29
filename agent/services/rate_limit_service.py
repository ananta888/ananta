from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from agent.redis import get_redis_client


class RateLimitService:
    """Shared rate-limit service for hub endpoints with Redis-first storage and in-memory fallback."""

    def __init__(self) -> None:
        self._in_memory: dict[str, list[float]] = defaultdict(list)

    def allow_request(self, *, namespace: str, subject: str, limit: int, window_seconds: int) -> bool:
        if limit <= 0 or window_seconds <= 0:
            return True
        key = self._build_key(namespace=namespace, subject=subject)
        redis_client = get_redis_client()
        if redis_client is not None:
            try:
                current = redis_client.get(key)
                if current and int(current) >= limit:
                    return False
                pipe = redis_client.pipeline()
                pipe.incr(key)
                pipe.expire(key, window_seconds)
                pipe.execute()
                return True
            except Exception as exc:
                logging.error("Redis error in rate limiting for %s: %s. Falling back to in-memory.", namespace, exc)
        return self._allow_request_in_memory(key=key, limit=limit, window_seconds=window_seconds)

    def describe_policy(self, *, namespace: str, limit: int, window_seconds: int) -> dict[str, Any]:
        return {
            "namespace": namespace,
            "limit": max(0, int(limit)),
            "window_seconds": max(0, int(window_seconds)),
            "storage": "redis_or_in_memory_fallback",
        }

    def clear_namespace(self, namespace: str) -> None:
        prefix = f"rate_limit:{namespace}:"
        for key in list(self._in_memory.keys()):
            if key.startswith(prefix):
                self._in_memory.pop(key, None)

    def _build_key(self, *, namespace: str, subject: str) -> str:
        safe_namespace = str(namespace or "default").strip().lower().replace(" ", "_")
        safe_subject = str(subject or "anonymous").strip() or "anonymous"
        return f"rate_limit:{safe_namespace}:{safe_subject}"

    def _allow_request_in_memory(self, *, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        bucket = self._in_memory.setdefault(key, [])
        self._in_memory[key] = [ts for ts in bucket if now - ts < window_seconds]
        if len(self._in_memory[key]) >= limit:
            return False
        self._in_memory[key].append(now)
        return True


rate_limit_service = RateLimitService()


def get_rate_limit_service() -> RateLimitService:
    return rate_limit_service
