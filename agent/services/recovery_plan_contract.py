"""Deterministic binding contract for approval-gated recovery plans."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    serializer = getattr(value, "model_dump", None)
    if callable(serializer):
        dumped = serializer()
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def calculate_recovery_materialization_inputs_digest(
    goal: Any,
) -> str:
    """Hash Goal-owned inputs used to derive a recovery child payload."""

    payload = {
        "schema": "ananta.recovery_materialization_inputs.v1",
        "goal_id": str(getattr(goal, "id", "") or ""),
        "goal": str(getattr(goal, "goal", "") or ""),
        "team_id": str(getattr(goal, "team_id", "") or ""),
        "mode": str(getattr(goal, "mode", "") or ""),
        "mode_data": _mapping(getattr(goal, "mode_data", None)),
        "execution_preferences": _mapping(
            getattr(goal, "execution_preferences", None)
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def calculate_recovery_plan_digest(plan: Any, nodes: list[Any]) -> str:
    """Bind an approval to every materialization-relevant plan field."""

    rationale = _mapping(getattr(plan, "rationale", None))
    payload = {
        "schema": "ananta.recovery_plan_digest.v2",
        "plan_id": str(getattr(plan, "id", "") or ""),
        "goal_id": str(getattr(plan, "goal_id", "") or ""),
        "trace_id": str(getattr(plan, "trace_id", "") or ""),
        "team_id": str(rationale.get("team_id") or ""),
        "materialization_inputs_digest": str(
            rationale.get("materialization_inputs_digest") or ""
        ),
        "planning_mode": str(getattr(plan, "planning_mode", "") or ""),
        "nodes": [
            {
                "id": str(getattr(node, "id", "") or ""),
                "node_key": str(getattr(node, "node_key", "") or ""),
                "title": str(getattr(node, "title", "") or ""),
                "description": str(getattr(node, "description", "") or ""),
                "priority": str(getattr(node, "priority", "") or ""),
                "position": int(getattr(node, "position", 0) or 0),
                "depends_on": [
                    str(value)
                    for value in list(getattr(node, "depends_on", None) or [])
                ],
                "editable": bool(getattr(node, "editable", True)),
                "rationale": _mapping(getattr(node, "rationale", None)),
                "verification_spec": _mapping(
                    getattr(node, "verification_spec", None)
                ),
            }
            for node in sorted(
                list(nodes or []),
                key=lambda value: (
                    int(getattr(value, "position", 0) or 0),
                    str(getattr(value, "id", "") or ""),
                ),
            )
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def calculate_recovery_task_payload_digest(task: Any) -> str:
    """Bind a released child to its approval-time execution payload."""

    payload = {
        "schema": "ananta.recovery_task_payload_digest.v1",
        "id": str(getattr(task, "id", "") or ""),
        "title": str(getattr(task, "title", "") or ""),
        "description": str(
            getattr(task, "description", "") or ""
        ),
        "priority": str(getattr(task, "priority", "") or ""),
        "goal_id": str(getattr(task, "goal_id", "") or ""),
        "plan_id": str(getattr(task, "plan_id", "") or ""),
        "plan_node_id": str(
            getattr(task, "plan_node_id", "") or ""
        ),
        "parent_task_id": str(
            getattr(task, "parent_task_id", "") or ""
        ),
        "source_task_id": str(
            getattr(task, "source_task_id", "") or ""
        ),
        "team_id": str(getattr(task, "team_id", "") or ""),
        "derivation_reason": str(
            getattr(task, "derivation_reason", "") or ""
        ),
        "derivation_depth": int(
            getattr(task, "derivation_depth", 0) or 0
        ),
        "task_kind": str(getattr(task, "task_kind", "") or ""),
        "retrieval_intent": str(
            getattr(task, "retrieval_intent", "") or ""
        ),
        "required_context_scope": str(
            getattr(task, "required_context_scope", "") or ""
        ),
        "preferred_bundle_mode": str(
            getattr(task, "preferred_bundle_mode", "") or ""
        ),
        "required_capabilities": [
            str(value)
            for value in list(
                getattr(task, "required_capabilities", None) or []
            )
        ],
        "context_bundle_id": str(
            getattr(task, "context_bundle_id", "") or ""
        ),
        "worker_execution_context": _mapping(
            getattr(task, "worker_execution_context", None)
        ),
        "worker_execution_contract": _mapping(
            getattr(task, "worker_execution_contract", None)
        ),
        "expected_artifacts": [
            dict(value)
            for value in list(
                getattr(task, "expected_artifacts", None) or []
            )
            if isinstance(value, dict)
        ],
        "verification_spec": _mapping(
            getattr(task, "verification_spec", None)
        ),
        "depends_on": [
            str(value)
            for value in list(
                getattr(task, "depends_on", None) or []
            )
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_recovery_dependency_binding(
    *,
    source_task_id: str,
    preexisting_dependency_ids: list[str] | tuple[str, ...],
    child_task_ids: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Bind the source's complete dependency list at materialization time."""

    def ordered_unique(values: list[str] | tuple[str, ...]) -> list[str]:
        return list(
            dict.fromkeys(
                str(value or "").strip()
                for value in values
                if str(value or "").strip()
            )
        )

    normalized_source_id = str(source_task_id or "").strip()
    preexisting = ordered_unique(preexisting_dependency_ids)
    children = ordered_unique(child_task_ids)
    if (
        not normalized_source_id
        or not children
        or len(children) != len(child_task_ids)
        or normalized_source_id in preexisting
        or normalized_source_id in children
        or set(preexisting).intersection(children)
    ):
        raise ValueError("recovery_dependency_binding_invalid")
    authoritative = [*preexisting, *children]
    digest_payload = {
        "schema": "ananta.recovery_dependency_binding_digest.v1",
        "source_task_id": normalized_source_id,
        "preexisting_dependency_ids": preexisting,
        "child_task_ids": children,
        "authoritative_dependency_ids": authoritative,
    }
    encoded = json.dumps(
        digest_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema": "ananta.recovery_dependency_binding.v1",
        "source_task_id": normalized_source_id,
        "preexisting_dependency_ids": preexisting,
        "child_task_ids": children,
        "authoritative_dependency_ids": authoritative,
        "digest": hashlib.sha256(encoded).hexdigest(),
    }
