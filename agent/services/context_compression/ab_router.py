"""HCCA-014: A/B routing for comparing compression strategies in real agent runs.

Routes compression requests deterministically to strategy A or B based on a
SHA-256 hash of (seed, content_id), enabling reproducible experiments.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ABRouterConfig:
    enabled: bool = False
    strategy_a: str = "passthrough"  # "passthrough" | "ananta_smart_compressor" | "external_headroom"
    strategy_b: str = "ananta_smart_compressor"
    rollout_percent_b: float = 0.0  # 0–100, % of requests routed to B
    seed: int = 42

    @classmethod
    def from_config(cls, config: dict | None = None) -> ABRouterConfig:
        if not config:
            return cls()
        return cls(
            enabled=bool(config.get("enabled", False)),
            strategy_a=str(config.get("strategy_a", "passthrough")),
            strategy_b=str(config.get("strategy_b", "ananta_smart_compressor")),
            rollout_percent_b=float(config.get("rollout_percent_b", 0.0)),
            seed=int(config.get("seed", 42)),
        )


class ABRouter:
    def __init__(self, config: ABRouterConfig) -> None:
        self._config = config
        self._total_a: int = 0
        self._total_b: int = 0

    def route(self, content_id: str) -> str:
        """Return "a" or "b" deterministically based on sha256(seed:content_id).

        B is chosen when hash_int % 100 < rollout_percent_b.
        If routing is disabled, always returns "a".
        """
        if not self._config.enabled:
            self._total_a += 1
            return "a"

        key = f"{self._config.seed}:{content_id}"
        digest = hashlib.sha256(key.encode()).digest()
        # Use the first 8 bytes as a big-endian unsigned integer.
        hash_int = int.from_bytes(digest[:8], byteorder="big")
        bucket = hash_int % 100

        if bucket < self._config.rollout_percent_b:
            self._total_b += 1
            return "b"
        self._total_a += 1
        return "a"

    def is_b(self, content_id: str) -> bool:
        """Return True if content_id is routed to strategy B."""
        return self.route(content_id) == "b"

    def stats(self) -> dict[str, Any]:
        """Return routing statistics."""
        return {
            "total_routed_a": self._total_a,
            "total_routed_b": self._total_b,
            "rollout_percent_b": self._config.rollout_percent_b,
            "enabled": self._config.enabled,
        }
