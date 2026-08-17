"""Task-specific root-cause routing with hard reasoning budgets."""

from __future__ import annotations

from typing import Any, Mapping

ROOT_CAUSE_KIND = "root_cause_investigation"

DEFAULT_POLICY = {
    "schema": "ananta.reasoning_execution_policy.v1",
    "max_thinking_steps": 6,
    "max_tool_calls": 8,
    "max_tokens": 4000,
    "allow_moe_offload": False,
    "fallback": "hybrid_retrieval",
}


def route_root_cause(task: Mapping[str, Any] | None = None, *, policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(task or {})
    kind = str(raw.get("task_kind") or raw.get("kind") or "").strip()
    resolved = dict(DEFAULT_POLICY)
    resolved.update(dict(policy or {}))
    if kind != ROOT_CAUSE_KIND:
        return {
            "routed": False,
            "reason": "not_root_cause_task",
            "fallback": resolved["fallback"],
            "policy": resolved,
        }
    return {
        "routed": True,
        "reason": "root_cause_investigation",
        "retrieval_profile": "evidence",
        "preserve_conflicts": True,
        "policy": resolved,
    }


def build_conflict_set(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "schema": "codecompass.conflict-set.v1",
        "task_kind": ROOT_CAUSE_KIND,
        "conflicts": [
            {"claim_a": left, "claim_b": right, "status": "unresolved", "evidence_ids": []}
            for left, right in pairs
        ],
    }
