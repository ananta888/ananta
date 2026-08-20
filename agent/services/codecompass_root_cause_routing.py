"""Task-specific root-cause routing with bounded backend ports."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping, Protocol

ROOT_CAUSE_KIND = "root_cause_investigation"

DEFAULT_POLICY = {
    "schema": "ananta.reasoning_execution_policy.v1",
    "max_thinking_steps": 6,
    "max_tool_calls": 8,
    "max_tokens": 4000,
    "allow_moe_offload": False,
    "fallback": "hybrid_retrieval",
}


class RootCauseBackendPort(Protocol):
    def analyze(
        self,
        *,
        task: Mapping[str, Any],
        policy: Mapping[str, Any],
        retrieval_profile: str,
        preserve_conflicts: bool,
    ) -> Mapping[str, Any]: ...


class RootCauseFallbackPort(Protocol):
    def retrieve(
        self,
        *,
        task: Mapping[str, Any],
        profile: str,
        policy: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


def _bounded_policy(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    requested = dict(policy or {})
    resolved = dict(DEFAULT_POLICY)
    for field in ("max_thinking_steps", "max_tool_calls", "max_tokens"):
        try:
            value = int(requested.get(field, resolved[field]))
        except (TypeError, ValueError):
            value = int(resolved[field])
        resolved[field] = max(1, min(value, int(DEFAULT_POLICY[field])))
    resolved["allow_moe_offload"] = bool(
        DEFAULT_POLICY["allow_moe_offload"] and requested.get("allow_moe_offload", False)
    )
    resolved["fallback"] = str(DEFAULT_POLICY["fallback"])
    return resolved


def _assert_usage_within_policy(result: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
    fields = {
        "thinking_steps": "max_thinking_steps",
        "tool_calls": "max_tool_calls",
        "tokens": "max_tokens",
    }
    for usage_field, policy_field in fields.items():
        try:
            used = int(usage.get(usage_field) or 0)
        except (TypeError, ValueError):
            raise ValueError("root_cause_backend_usage_invalid") from None
        if used < 0 or used > int(policy[policy_field]):
            raise ValueError(f"root_cause_budget_exceeded:{usage_field}")


def route_root_cause(
    task: Mapping[str, Any] | None = None,
    *,
    policy: Mapping[str, Any] | None = None,
    backend: RootCauseBackendPort | None = None,
    fallback_backend: RootCauseFallbackPort | None = None,
) -> dict[str, Any]:
    raw = dict(task or {})
    kind = str(raw.get("task_kind") or raw.get("kind") or "").strip()
    resolved = _bounded_policy(policy)
    readonly_policy = MappingProxyType(resolved)
    if kind != ROOT_CAUSE_KIND:
        response = {
            "routed": False,
            "reason": "not_root_cause_task",
            "fallback": resolved["fallback"],
            "policy": resolved,
        }
        if fallback_backend is not None:
            execution = fallback_backend.retrieve(
                task=MappingProxyType(raw),
                profile=resolved["fallback"],
                policy=readonly_policy,
            )
            response["execution"] = dict(execution)
        return response
    response = {
        "routed": True,
        "reason": "root_cause_investigation",
        "retrieval_profile": "evidence",
        "preserve_conflicts": True,
        "policy": resolved,
        "execution_status": "backend_unavailable" if backend is None else "completed",
    }
    if backend is not None:
        execution = backend.analyze(
            task=MappingProxyType(raw),
            policy=readonly_policy,
            retrieval_profile="evidence",
            preserve_conflicts=True,
        )
        if not isinstance(execution, Mapping):
            raise ValueError("root_cause_backend_result_invalid")
        _assert_usage_within_policy(execution, resolved)
        response["execution"] = dict(execution)
    return response


def evaluate_root_cause_routing(result: Mapping[str, Any]) -> dict[str, Any]:
    policy = dict(result.get("policy") or {})
    execution = dict(result.get("execution") or {})
    try:
        _assert_usage_within_policy(execution, policy)
        budget_respected = True
    except ValueError:
        budget_respected = False
    return {
        "schema": "codecompass.root-cause-routing-evaluation.v1",
        "eligible_routed": bool(result.get("routed")),
        "budget_respected": budget_respected,
        "conflicts_preserved": bool(result.get("preserve_conflicts")),
        "fallback_selected": str(result.get("fallback") or ""),
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
