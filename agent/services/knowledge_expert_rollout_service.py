"""Hub-owned default-off, canary and GA gates for knowledge experts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any


class KnowledgeExpertRolloutService:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self._config = dict(config)
        if self._config.get("schema") != "ananta.knowledge-expert-rollout.v1":
            raise ValueError("knowledge_expert_rollout_config_invalid")
        self._stage = str(self._config.get("stage") or "off")
        self._enabled = self._config.get("enabled") is True
        self._percent = int(self._config.get("canary_percent") or 0)
        self._required = tuple(str(item) for item in self._config.get("required_gates") or ())
        self._gates = dict(self._config.get("gate_status") or {})
        if self._stage not in {"off", "shadow", "canary", "ga"} or not 0 <= self._percent <= 100:
            raise ValueError("knowledge_expert_rollout_config_invalid")
        if self._config.get("fallback_mode") != "rag_only" or set(self._required) != set(self._gates):
            raise ValueError("knowledge_expert_rollout_config_invalid")

    def routing_enabled(self, *, tenant_id: str) -> bool:
        if not self._enabled or self._stage in {"off", "shadow"}:
            return False
        if not self._all_gates_passed():
            return False
        if self._stage == "ga":
            return True
        bucket = int(hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:8], 16) % 100
        return bucket < self._percent

    def assert_stage_allowed(self, requested_stage: str) -> None:
        if requested_stage not in {"off", "shadow", "canary", "ga"}:
            raise ValueError("knowledge_expert_rollout_stage_invalid")
        if requested_stage in {"canary", "ga"} and not self._all_gates_passed():
            raise ValueError("knowledge_expert_rollout_gates_blocked")

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": "ananta.knowledge-expert-rollout-status.v1",
            "enabled": self._enabled,
            "stage": self._stage,
            "gate_status": dict(self._gates),
            "ga_allowed": self._all_gates_passed(),
            "fallback_mode": "rag_only",
        }

    def _all_gates_passed(self) -> bool:
        return bool(self._required) and all(self._gates.get(gate) == "passed" for gate in self._required)


__all__ = ["KnowledgeExpertRolloutService"]
