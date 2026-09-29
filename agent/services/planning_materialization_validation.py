"""Pre-materialization validation for persisted plans.

Split out of ``planning_service`` (SRP): the dependency contract of plan
nodes, the re-run of proposal/DAG/quality gates after review and the exact
ownership check of deterministic plan tasks. ``PlanningService`` keeps the
method names and delegates here.
"""

from __future__ import annotations

from typing import Any

from agent.db_models import PlanDB, PlanNodeDB
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.planning_proposal_service import build_plan_proposal, validate_plan_proposal_payload
from agent.services.planning_quality_service import get_planning_quality_service


def task_matches_materialization_binding(
    task: Any,
    *,
    plan: PlanDB,
    node: PlanNodeDB,
    task_id: str,
    team_id: str | None,
    parent_task_id: str | None,
    source_task_id: str | None,
    depends_on: list[str],
    initial_task_status: str,
) -> bool:
    """Check the immutable ownership fields of a deterministic plan task."""

    expected_source = (
        source_task_id
        if source_task_id is not None
        else parent_task_id
    )
    expected_depth = 1 if (parent_task_id or source_task_id) else 0
    rationale = dict(node.rationale or {})
    expected = {
        "id": str(task_id),
        "goal_id": str(plan.goal_id or ""),
        "goal_trace_id": str(plan.trace_id or ""),
        "plan_id": str(plan.id or ""),
        "plan_node_id": str(node.id or ""),
        "team_id": str(team_id or ""),
        "parent_task_id": str(parent_task_id or ""),
        "source_task_id": str(expected_source or ""),
        "derivation_reason": f"goal_{plan.planning_mode or 'planning'}",
        "derivation_depth": int(expected_depth),
        "status": str(initial_task_status or "todo").strip().lower(),
        "title": str(node.title or ""),
        "description": str(node.description or ""),
        "priority": str(node.priority or ""),
        "task_kind": str(rationale.get("task_kind") or ""),
        "retrieval_intent": str(
            rationale.get("retrieval_intent") or ""
        ),
        "required_context_scope": str(
            rationale.get("required_context_scope") or ""
        ),
        "preferred_bundle_mode": str(
            rationale.get("preferred_bundle_mode") or ""
        ),
    }
    actual = {
        "id": str(getattr(task, "id", "") or ""),
        "goal_id": str(getattr(task, "goal_id", "") or ""),
        "goal_trace_id": str(
            getattr(task, "goal_trace_id", "") or ""
        ),
        "plan_id": str(getattr(task, "plan_id", "") or ""),
        "plan_node_id": str(
            getattr(task, "plan_node_id", "") or ""
        ),
        "team_id": str(getattr(task, "team_id", "") or ""),
        "parent_task_id": str(
            getattr(task, "parent_task_id", "") or ""
        ),
        "source_task_id": str(
            getattr(task, "source_task_id", "") or ""
        ),
        "derivation_reason": str(
            getattr(task, "derivation_reason", "") or ""
        ),
        "derivation_depth": int(
            getattr(task, "derivation_depth", 0) or 0
        ),
        "status": str(getattr(task, "status", "") or "")
        .strip()
        .lower(),
        "title": str(getattr(task, "title", "") or ""),
        "description": str(
            getattr(task, "description", "") or ""
        ),
        "priority": str(getattr(task, "priority", "") or ""),
        "task_kind": str(
            getattr(task, "task_kind", "") or ""
        ),
        "retrieval_intent": str(
            getattr(task, "retrieval_intent", "") or ""
        ),
        "required_context_scope": str(
            getattr(task, "required_context_scope", "") or ""
        ),
        "preferred_bundle_mode": str(
            getattr(task, "preferred_bundle_mode", "") or ""
        ),
    }
    return (
        actual == expected
        and list(getattr(task, "depends_on", None) or [])
        == list(depends_on or [])
        and list(
            getattr(task, "required_capabilities", None) or []
        )
        == list(rationale.get("required_capabilities") or [])
        and dict(getattr(task, "verification_spec", None) or {})
        == dict(node.verification_spec or {})
    )


def validate_existing_plan_for_materialization(
    service,
    *,
    plan: PlanDB,
    nodes: list[PlanNodeDB],
    team_id: str | None,
) -> dict[str, Any]:
    """Re-run deterministic proposal, DAG, and quality gates after review."""
    dependency_error = service._dependency_contract_error(nodes)
    if dependency_error is not None:
        return {
            "ok": False,
            "reason_code": dependency_error,
        }
    key_to_position = {
        str(node.node_key): str(index)
        for index, node in enumerate(nodes, start=1)
    }
    subtasks: list[dict[str, Any]] = []
    for node in nodes:
        rationale = dict(node.rationale or {})
        subtasks.append(
            {
                "title": str(node.title or ""),
                "description": str(node.description or ""),
                "priority": str(node.priority or "Medium"),
                "task_kind": str(rationale.get("task_kind") or "coding"),
                "depends_on": [
                    key_to_position[dep]
                    for dep in list(node.depends_on or [])
                ],
                "required_capabilities": [
                    str(item)
                    for item in list(rationale.get("required_capabilities") or [])
                    if str(item).strip()
                ],
                "expected_artifacts": [
                    dict(item)
                    for item in list(rationale.get("expected_artifacts") or [])
                    if isinstance(item, dict)
                ],
                "verification_spec": dict(node.verification_spec or {}),
            }
        )

    scoped = get_goal_config_runtime_service().get_effective_config(
        goal_id=plan.goal_id,
        task_id=None,
    )
    planning_policy = service._resolve_planning_policy(dict(scoped.config or {}))
    quality = get_planning_quality_service().evaluate(
        subtasks=subtasks,
        mode=str(plan.planning_mode or "generic"),
        planning_policy=planning_policy,
        team_id=team_id,
    )
    remaining_reasons = [
        item
        for item in str(quality.reason or "").split("|")
        if item and item != "ok"
    ]
    soft_only = bool(subtasks) and remaining_reasons and all(
        item.startswith("missing_categories:")
        for item in remaining_reasons
    )
    if not quality.ok and not soft_only:
        return {
            "ok": False,
            "reason_code": "planning_quality_gate_failed",
            "quality_reason": str(quality.reason or ""),
            "missing_categories": list(quality.missing_categories or []),
            "generic_task_indices": list(quality.generic_task_indices or []),
        }

    proposal = build_plan_proposal(
        goal_id=plan.goal_id,
        trace_id=plan.trace_id,
        summary=f"Approved recovery plan {plan.id}",
        subtasks=subtasks,
        required_capabilities=[],
    )
    known_capabilities = {
        "planning",
        "coding",
        "testing",
        "review",
        "research",
        "ops",
        "analysis",
        "doc",
        "plan.propose",
        "risk.estimate",
        "dependencies.suggest",
        "clarifying_questions.suggest",
    }
    proposal_validation = validate_plan_proposal_payload(
        proposal,
        known_capabilities=known_capabilities,
    )
    if not proposal_validation.ok:
        return {
            "ok": False,
            "reason_code": "invalid_plan_proposal",
            "proposal_validation_errors": list(proposal_validation.errors or []),
        }
    if service._prepare_materialization(nodes=nodes) is None:
        return {
            "ok": False,
            "reason_code": "invalid_dependencies",
        }
    return {
        "ok": True,
        "reason_code": "validated",
        "quality_soft_accepted": bool(soft_only),
    }


def dependency_contract_error(
    nodes: list[PlanNodeDB],
) -> str | None:
    node_keys = [str(node.node_key or "").strip() for node in nodes]
    if any(not key for key in node_keys):
        return "plan_node_key_missing"
    if len(set(node_keys)) != len(node_keys):
        return "plan_node_key_duplicate"
    known = set(node_keys)
    for node in nodes:
        dependencies = [
            str(value or "").strip()
            for value in list(node.depends_on or [])
        ]
        if len(set(dependencies)) != len(dependencies):
            return "plan_dependency_duplicate"
        if str(node.node_key) in dependencies:
            return "plan_dependency_self_reference"
        if any(not dependency or dependency not in known for dependency in dependencies):
            return "plan_dependency_unknown"
    return None
