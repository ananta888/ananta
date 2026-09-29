"""Plan node construction, generation limits and draft-plan persistence.

Split out of ``planning_service`` (SRP): turning planner subtasks into
``PlanNodeDB`` rows (dependencies, capabilities, verification defaults),
bounding plan size/depth and persisting a draft plan with its nodes.
``PlanningService`` keeps the method names and delegates here.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from agent.db_models import PlanDB, PlanNodeDB
from agent.services.planning_feature_flags import get_goal_feature_flags, get_plan_generation_limits
from agent.services.planning_subtask_sanitizer import (
    infer_subtask_task_kind,
    merge_verification_defaults,
    retrieval_hints_for_task_kind,
    sanitize_blueprint_provenance,
    sanitize_role_defaults,
)
from agent.services.verification_policy_service import default_verification_spec
from agent.services.worker_routing_policy_utils import (
    derive_required_capabilities,
    merge_capabilities_with_blueprint_defaults,
)


def _facade():
    """Resolve patchable collaborators through the public ``planning_service`` entry point."""
    import agent.services.planning_service as facade_module

    return facade_module


def compute_plan_depth(service, probe_nodes: list[PlanNodeDB]) -> int:
    depth_by_key: dict[str, int] = {}
    max_depth = 0
    for node in probe_nodes:
        if not node.depends_on:
            depth = 1
        else:
            depth = 1 + max(depth_by_key.get(dep, 1) for dep in node.depends_on)
        depth_by_key[node.node_key] = depth
        if depth > max_depth:
            max_depth = depth
    return max_depth


def apply_plan_generation_limits(
    service, subtasks: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int], str | None]:
    limits = get_plan_generation_limits()
    bounded = [dict(subtask or {}) for subtask in (subtasks or [])]
    node_count = len(bounded)
    for subtask in bounded:
        raw_deps = list(subtask.get("depends_on") or [])
        raw_mode = str(subtask.get("dependency_mode") or "").strip().lower()
        if raw_mode not in {"parallel", "explicit", "sequential"}:
            raw_mode = "explicit" if raw_deps else "sequential"
        if "__parallel__" in raw_deps:
            subtask["dependency_mode"] = "parallel"
            subtask["depends_on"] = []
            continue
        depends_on = []
        for dep in raw_deps:
            dep_text = str(dep).strip()
            if not dep_text:
                continue
            if dep_text.isdigit() and int(dep_text) <= node_count:
                depends_on.append(dep_text)
            elif dep_text in {f"{index}" for index in range(1, node_count + 1)}:
                depends_on.append(dep_text)
        if depends_on:
            subtask["depends_on"] = depends_on
            subtask["dependency_mode"] = "explicit"
        elif "depends_on" in subtask:
            subtask.pop("depends_on", None)
            subtask["dependency_mode"] = "parallel" if raw_mode == "parallel" else "sequential"

    limits = {
        **limits,
        "observed_plan_nodes": node_count,
    }
    if node_count > limits["max_plan_nodes"]:
        return bounded[: limits["max_plan_nodes"]], {**limits, "truncated": True}, "max_plan_nodes"

    observed_depth = service._compute_plan_depth(service._build_nodes("plan-limit-probe", bounded, "limit_probe"))
    limits = {**limits, "observed_plan_depth": observed_depth}
    if observed_depth > limits["max_plan_depth"]:
        return bounded, limits, "max_plan_depth"
    return bounded, limits, None


def build_nodes(service, plan_id: str, subtasks: list[dict], planning_mode: str) -> list[PlanNodeDB]:
    nodes: list[PlanNodeDB] = []
    node_keys: list[str] = []

    for index, subtask in enumerate(subtasks, start=1):
        node_key = f"{plan_id}-node-{index}"
        node_keys.append(node_key)
        task_kind = infer_subtask_task_kind(subtask)
        retrieval_hints = retrieval_hints_for_task_kind(task_kind)
        blueprint_provenance = sanitize_blueprint_provenance(subtask)
        role_defaults = sanitize_role_defaults(subtask)
        required_capabilities = derive_required_capabilities(
            {
                "title": str(subtask.get("title") or ""),
                "description": str(subtask.get("description") or ""),
                "task_kind": task_kind,
            },
            task_kind,
        )
        required_capabilities = merge_capabilities_with_blueprint_defaults(
            required_capabilities,
            {"blueprint_role_defaults": role_defaults},
        )
        raw_depends_on = list(subtask.get("depends_on") or [])
        dependency_mode = str(subtask.get("dependency_mode") or "").strip().lower()
        if dependency_mode not in {"parallel", "explicit", "sequential"}:
            dependency_mode = "explicit" if raw_depends_on else "sequential"
        if "__parallel__" in raw_depends_on:
            dependency_mode = "parallel"
            raw_depends_on = []
        is_parallel = dependency_mode == "parallel"
        depends_on: list[str] = []
        if raw_depends_on and dependency_mode == "explicit":
            for dep in raw_depends_on:
                dep_text = str(dep).strip()
                if dep_text in node_keys:
                    depends_on.append(dep_text)
                elif dep_text.isdigit():
                    dep_index = int(dep_text) - 1
                    if 0 <= dep_index < len(node_keys):
                        depends_on.append(node_keys[dep_index])
        elif dependency_mode == "sequential" and index > 1:
            depends_on.append(node_keys[index - 2])

        nodes.append(
            PlanNodeDB(
                plan_id=plan_id,
                node_key=node_key,
                title=str(subtask.get("title") or f"Step {index}")[:200],
                description=str(subtask.get("description") or subtask.get("title") or "")[:2000],
                priority=str(subtask.get("priority") or "Medium"),
                position=index,
                depends_on=depends_on,
                rationale={
                    "planning_mode": planning_mode,
                    "task_kind": task_kind,
                    "retrieval_intent": retrieval_hints["retrieval_intent"],
                    "required_context_scope": retrieval_hints["required_context_scope"],
                    "preferred_bundle_mode": retrieval_hints["preferred_bundle_mode"],
                    "required_capabilities": required_capabilities,
                    "source_depends_on": raw_depends_on,
                    "dependency_mode": dependency_mode,
                    "artifact_trace_id": str(subtask.get("artifact_trace_id") or f"A{index}"),
                    "expected_artifacts": [dict(a) for a in list(subtask.get("expected_artifacts") or []) if isinstance(a, dict)],
                    "artifact": subtask.get("artifact"),
                    "risk_focus": subtask.get("risk_focus"),
                    "test_focus": subtask.get("test_focus"),
                    "review_focus": subtask.get("review_focus"),
                    **({"parallel": True} if is_parallel else {}),
                    **blueprint_provenance,
                    **({"blueprint_role_defaults": role_defaults} if role_defaults else {}),
                },
                verification_spec=merge_verification_defaults(
                    default_verification_spec(
                        {
                            "task_kind": task_kind,
                            "title": subtask.get("title"),
                            "description": subtask.get("description"),
                        }
                    ),
                    role_defaults,
                ),
            )
        )
    return nodes


def persist_plan(
    service,
    goal_id: str,
    trace_id: str,
    subtasks: list[dict],
    planning_mode: str,
    raw_response: Optional[str],
    context: Optional[str],
    planning_origin: str | None = None,
    repair_strategy_used: str | None = None,
    repair_attempt_count: int = 0,
    parse_mode: str | None = None,
    planning_run_id: str | None = None,
    initial_rationale: dict[str, Any] | None = None,
) -> tuple[PlanDB | None, list[PlanNodeDB]]:
    repos = _facade().get_repository_registry()
    flags = get_goal_feature_flags()
    if not flags.get("persisted_plans_enabled", True):
        return None, []

    plan = PlanDB(
        goal_id=goal_id,
        trace_id=trace_id,
        status="draft",
        planning_mode=planning_mode,
        rationale={
            "planning_mode": planning_mode,
            "planning_origin": planning_origin or planning_mode,
            "repair_strategy_used": repair_strategy_used,
            "repair_attempt_count": int(repair_attempt_count or 0),
            "parse_mode": parse_mode,
            "planning_run_id": planning_run_id,
            "node_count": len(subtasks),
            "context_used": bool(context),
            "raw_response_preview": (raw_response or "")[:400],
            **dict(initial_rationale or {}),
        },
    )
    plan = repos.plan_repo.save(plan)
    repos.plan_node_repo.delete_by_plan_id(plan.id)
    nodes = service._build_nodes(plan.id, subtasks, planning_mode)
    try:
        for node in nodes:
            repos.plan_node_repo.save(node)
    except Exception as exc:
        repos.plan_node_repo.delete_by_plan_id(plan.id)
        plan.status = "failed"
        plan.rationale = {
            **(plan.rationale or {}),
            "persist_error": str(exc)[:500],
        }
        plan.updated_at = time.time()
        repos.plan_repo.save(plan)
        logging.getLogger("agent.services.planning_service").warning("Plan persistence failed for %s: %s", plan.id, exc)
        return plan, []
    return plan, repos.plan_node_repo.get_by_plan_id(plan.id)
