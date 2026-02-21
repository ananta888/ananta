"""
Goal-based LLM response caching for similar goals.
Uses fuzzy matching to find similar goals and return cached responses.
"""

import hashlib
import logging
import threading
import time
from typing import Optional

from flask import current_app


class GoalCache:
    """LRU cache for LLM responses based on goal similarity."""

    def __init__(self, max_size: int = 100, ttl_seconds: int = 3600, similarity_threshold: float = 0.85):
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._similarity_threshold = similarity_threshold
        self._stats = {"hits": 0, "misses": 0, "evictions": 0, "similarity_matches": 0}

    def _normalize_goal(self, goal: str) -> str:
        normalized = " ".join(goal.lower().split())
        normalized = "".join(c for c in normalized if c.isalnum() or c.isspace())
        return normalized

    def _compute_key(self, goal: str, context: Optional[str] = None) -> str:
        normalized = self._normalize_goal(goal)
        combined = normalized
        if context:
            combined += "|" + self._normalize_goal(context)
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    def _jaccard_similarity(self, s1: str, s2: str) -> float:
        words1 = set(s1.split())
        words2 = set(s2.split())
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    def _find_similar_key(self, goal: str, context: Optional[str] = None) -> Optional[str]:
        normalized = self._normalize_goal(goal)
        best_key = None
        best_similarity = 0.0

        for key, entry in self._cache.items():
            if time.time() - entry.get("timestamp", 0) > self._ttl:
                continue

            cached_goal = entry.get("normalized_goal", "")
            similarity = self._jaccard_similarity(normalized, cached_goal)

            if similarity >= self._similarity_threshold and similarity > best_similarity:
                best_similarity = similarity
                best_key = key

        if best_key:
            self._stats["similarity_matches"] += 1
        return best_key

    def get(self, goal: str, context: Optional[str] = None) -> Optional[dict]:
        key = self._compute_key(goal, context)

        with self._lock:
            entry = self._cache.get(key)
            if entry and time.time() - entry.get("timestamp", 0) <= self._ttl:
                self._stats["hits"] += 1
                logging.debug(f"Goal cache hit for key {key}")
                return entry.get("response")

            similar_key = self._find_similar_key(goal, context)
            if similar_key:
                entry = self._cache.get(similar_key)
                if entry and time.time() - entry.get("timestamp", 0) <= self._ttl:
                    self._stats["hits"] += 1
                    logging.debug(f"Goal cache similarity hit for {similar_key}")
                    return entry.get("response")

            self._stats["misses"] += 1
            return None

    def set(self, goal: str, response: dict, context: Optional[str] = None):
        key = self._compute_key(goal, context)

        with self._lock:
            if len(self._cache) >= self._max_size and key not in self._cache:
                self._evict_oldest()

            self._cache[key] = {
                "response": response,
                "timestamp": time.time(),
                "goal": goal,
                "context": context,
                "normalized_goal": self._normalize_goal(goal),
            }
            logging.debug(f"Goal cache set for key {key}")

    def _evict_oldest(self):
        if not self._cache:
            return

        oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].get("timestamp", 0))
        del self._cache[oldest_key]
        self._stats["evictions"] += 1

    def clear(self):
        with self._lock:
            self._cache.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            hit_rate = self._stats["hits"] / total if total > 0 else 0.0
            return {**self._stats, "size": len(self._cache), "max_size": self._max_size, "hit_rate": round(hit_rate, 3)}


_goal_cache: Optional[GoalCache] = None


def get_goal_cache() -> GoalCache:
    global _goal_cache
    if _goal_cache is None:
        try:
            cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("goal_cache", {}) or {}
        except RuntimeError:
            cfg = {}
        _goal_cache = GoalCache(
            max_size=cfg.get("max_size", 100),
            ttl_seconds=cfg.get("ttl_seconds", 3600),
            similarity_threshold=cfg.get("similarity_threshold", 0.85),
        )
    return _goal_cache
