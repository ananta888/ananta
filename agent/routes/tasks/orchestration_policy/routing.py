from __future__ import annotations

from random import random

from agent.services.worker_routing_policy_utils import (
    derive_required_capabilities as _derive_required_capabilities,
    derive_research_specialization,
    normalize_capabilities as _normalize_capabilities,
)

from .models import WorkerSelection

ROLE_CAPABILITY_MAP = {
    "planner": {"planning", "task_graph", "analysis"},
    "researcher": {"research", "analysis"},
    "coder": {"coding", "implementation"},
    "reviewer": {"review", "analysis"},
    "tester": {"testing", "verification"},
    "repairer": {"deterministic_repair", "admin_repair", "shell_execute", "verify"},
}

# WFG-009: mapping from blueprint workflow roles (defined in
# proposed_blueprint_contract.workflow.steps[].role) to the set of
# worker roles that may serve them. Worker roles are kept on the
# existing {planner, researcher, coder, reviewer, tester, repairer}
# enum so no worker registration needs to change.
WORKFLOW_ROLE_TO_WORKER_ROLES: dict[str, tuple[str, ...]] = {
    "product_owner": ("planner", "reviewer"),
    "planner": ("planner",),
    "scrum_master": ("reviewer", "planner"),
    "developer": ("coder",),
    "qa_verifier": ("tester", "reviewer"),
    "security_reviewer": ("reviewer", "tester"),
    "coordinator": ("planner", "reviewer"),
    "researcher": ("researcher",),
    "documenter": ("reviewer",),
    "default": ("coder", "reviewer", "tester", "planner"),
}

# WFG-009: extended task-kind preferences. Adds the task_kind values
# introduced by the workflow contract (gate_review, security_review,
# handoff, documentation). Existing values are unchanged.
TASK_KIND_ROLE_PREFERENCES = {
    "planning": ["planner"],
    "research": ["researcher"],
    "coding": ["coder"],
    "review": ["reviewer"],
    "testing": ["tester"],
    "verification": ["tester", "reviewer"],
    "admin_repair": ["repairer"],
    "deterministic_repair": ["repairer"],
    "gate_review": ["reviewer", "planner"],
    "security_review": ["reviewer", "tester"],
    "handoff": ["reviewer", "planner"],
    "documentation": ["reviewer"],
}

SECURITY_LEVEL_RANK = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

def normalize_capabilities(capabilities: list[str] | None) -> list[str]:
    return _normalize_capabilities(capabilities)


def normalize_worker_roles(worker_roles: list[str] | None) -> list[str]:
    allowed = {"planner", "researcher", "coder", "reviewer", "tester"}
    normalized: list[str] = []
    for role in worker_roles or []:
        value = str(role or "").strip().lower()
        if value in allowed and value not in normalized:
            normalized.append(value)
    return normalized


def derive_required_capabilities(task: dict | None, task_kind: str | None = None) -> list[str]:
    return _derive_required_capabilities(task, task_kind)


def _resolve_workflow_step(task: dict | None) -> dict | None:
    """WFG-009: pull the workflow_step provenance written by
    PlanningTrackTaskIntegrationService (WFG-007) onto the task.

    Returns the dict as-is, or None if the task is not backed by a
    blueprint workflow step (legacy tasks, ad-hoc planner output,
    etc.). Lookup is intentionally permissive: we check both the
    worker_execution_context.workflow_step block and the legacy
    blueprint_* keys that BlueprintPlanningAdapter (WFG-006) writes
    onto subtasks.
    """
    if not isinstance(task, dict):
        return None
    ctx = task.get("worker_execution_context")
    if isinstance(ctx, dict):
        step = ctx.get("workflow_step")
        if isinstance(step, dict):
            return step
    if task.get("blueprint_workflow_step_id"):
        return {
            "step_id": task.get("blueprint_workflow_step_id"),
            "step_label": task.get("blueprint_workflow_step_id_label") or task.get("blueprint_workflow_step_id"),
            "role": task.get("blueprint_role_name"),
            "task_kind": task.get("task_kind"),
            "gate": bool(task.get("gate", False)),
            "required_capabilities": list(task.get("required_capabilities") or []),
        }
    return None


def choose_worker_for_task(
    task: dict | None,
    workers: list[dict],
    task_kind: str | None = None,
    required_capabilities: list[str] | None = None,
    workflow_step: dict | None = None,
) -> WorkerSelection:
    # WFG-009: explicit workflow_step wins over task-derived lookup.
    resolved_step = workflow_step if isinstance(workflow_step, dict) else _resolve_workflow_step(task)
    workflow_provided_required: list[str] = []
    if resolved_step:
        step_task_kind = str(resolved_step.get("task_kind") or "").strip()
        if step_task_kind:
            task_kind = step_task_kind
        step_required = list(resolved_step.get("required_capabilities") or [])
        if step_required:
            required_capabilities = step_required
            workflow_provided_required = _normalize_capabilities(step_required)
    normalized_required = _normalize_capabilities(required_capabilities) or derive_required_capabilities(task, task_kind)
    kind = str(task_kind or (task or {}).get("task_kind") or "").strip().lower()
    preferred_roles = list(TASK_KIND_ROLE_PREFERENCES.get(kind, []))
    # WFG-009: when a workflow step is present, role preferences are
    # unioned with the workflow role's worker-role mapping so that a
    # gate_review step driven by a scrum_master workflow role picks
    # planner+reviewer workers (not just the task_kind default).
    workflow_role = str((resolved_step or {}).get("role") or "").strip().lower()
    if workflow_role:
        role_targets = WORKFLOW_ROLE_TO_WORKER_ROLES.get(
            workflow_role, WORKFLOW_ROLE_TO_WORKER_ROLES["default"]
        )
        for role in role_targets:
            if role not in preferred_roles:
                preferred_roles.append(role)
    required_security = _security_level(task or {})

    ranked: list[tuple[float, dict, list[str], list[str], float, float, str]] = []
    for worker in workers:
        liveness = dict(worker.get("liveness") or {})
        worker_status = str(worker.get("status") or liveness.get("status") or "").lower()
        if worker.get("available_for_routing") is False or liveness.get("available_for_routing") is False:
            continue
        if worker_status != "online":
            continue
        if worker.get("registration_validated") is False:
            continue

        execution_limits = dict(worker.get("execution_limits") or {})
        max_parallel = int(execution_limits.get("max_parallel_tasks") or 0)
        current_load = int(worker.get("current_load") or 0)
        if max_parallel > 0 and current_load >= max_parallel:
            continue

        worker_security = _security_level(worker)
        if worker_security < required_security:
            continue

        worker_roles = normalize_worker_roles(worker.get("worker_roles"))
        worker_caps = _normalize_capabilities(worker.get("capabilities"))
        expanded_caps = set(worker_caps)
        for role in worker_roles:
            expanded_caps.update(ROLE_CAPABILITY_MAP.get(role, set()))

        matched_caps = [cap for cap in normalized_required if cap in expanded_caps]
        matched_roles = [role for role in worker_roles if role in preferred_roles]
        if normalized_required and not matched_caps:
            continue

        load_penalty = _load_ratio(current_load, max_parallel)
        success_signal, quality_signal = _quality_signals(worker)

        score = (
            len(matched_caps) * 50.0
            + len(matched_roles) * 20.0
            + (success_signal * 20.0)
            + (quality_signal * 10.0)
            + (worker_security * 2.0)
            - (load_penalty * 30.0)
        )
        ranked.append((score, worker, matched_caps, matched_roles, load_penalty, success_signal, _security_label(worker_security)))

    if resolved_step and workflow_provided_required:
        # WFG-009: when a workflow step lists required_capabilities,
        # the workflow contract is the source of truth. Heuristic
        # capability derivation (text mining) must not widen it,
        # otherwise a step like gate_review with required_capabilities
        # ["gate.review"] could match a coder with capability "coding"
        # because derive_required_capabilities() falls back to
        # ["coding"] when it does not recognize the task_kind.
        normalized_required = workflow_provided_required

    if ranked:
        ranked.sort(key=lambda item: (-item[0], item[4], -(item[5]), item[1].get("url") or ""))
        _, selected, matched_caps, matched_roles, load_penalty, success_signal, security_label = ranked[0]
        return WorkerSelection(
            worker_url=str(selected.get("url") or ""),
            reasons=[
                f"matched_capabilities:{','.join(matched_caps)}" if matched_caps else "matched_capabilities:none",
                f"matched_roles:{','.join(matched_roles)}" if matched_roles else "matched_roles:none",
                f"load_ratio:{load_penalty:.2f}",
                f"success_signal:{success_signal:.2f}",
                f"security_level:{security_label}",
            ]
            + (
                [
                    f"workflow_step_id:{resolved_step.get('step_id') or ''}",
                    f"workflow_step_role:{workflow_role}",
                    f"workflow_task_kind:{kind}",
                    f"routing_origin:workflow_role_mapping",
                ]
                if resolved_step
                else []
            ),
            matched_capabilities=matched_caps,
            matched_roles=matched_roles,
            strategy="capability_quality_load_match",
            workflow_step_id=str((resolved_step or {}).get("step_id") or "") or None,
            workflow_step_role=workflow_role or None,
            workflow_task_kind=(kind or None) if resolved_step else None,
            routing_origin="workflow_role_mapping" if resolved_step else None,
        )

    if resolved_step and workflow_provided_required and not ranked:
        # WFG-009: a workflow step with required_capabilities that no
        # online worker can satisfy must NOT silently fall back to an
        # unrelated worker. Return workflow_blocked so the caller can
        # surface a pending_with_reason or escalate to a human
        # approval flow (WFG-024).
        return WorkerSelection(
            worker_url=None,
            reasons=[
                "workflow_capability_not_satisfied",
                f"workflow_step_id:{resolved_step.get('step_id') or ''}",
                f"workflow_step_role:{workflow_role}",
                f"workflow_task_kind:{kind}",
                f"required_capabilities:{','.join(normalized_required)}",
            ],
            matched_capabilities=[],
            matched_roles=[],
            strategy="workflow_blocked",
            workflow_step_id=str(resolved_step.get("step_id") or "") or None,
            workflow_step_role=workflow_role or None,
            workflow_task_kind=kind or None,
            routing_origin="workflow_blocked",
        )

    fallback = _pick_fallback_worker(workers, min_security=required_security)
    if fallback:
        fallback_load_ratio = _load_ratio(
            int(fallback.get("current_load") or 0),
            int((fallback.get("execution_limits") or {}).get("max_parallel_tasks") or 0),
        )
        return WorkerSelection(
            worker_url=str(fallback.get("url") or ""),
            reasons=[
                "fallback:least_loaded_online_worker",
                f"required_capabilities:{','.join(normalized_required)}",
                f"load_ratio:{fallback_load_ratio:.2f}",
            ]
            + (
                [f"workflow_step_id:{resolved_step.get('step_id') or ''}", "routing_origin:workflow_fallback"]
                if resolved_step
                else []
            ),
            matched_capabilities=[],
            matched_roles=[],
            strategy="fallback",
            workflow_step_id=str((resolved_step or {}).get("step_id") or "") or None,
            workflow_step_role=workflow_role or None,
            workflow_task_kind=(kind or None) if resolved_step else None,
            routing_origin="workflow_fallback" if resolved_step else None,
        )
    # WFG-009: workflow_blocked is handled above (when no worker
    # matches the workflow_step's required_capabilities). For legacy
    # tasks without a workflow_step, fall through to the historical
    # no_online_worker_available path.
    return WorkerSelection(
        worker_url=None,
        reasons=["no_online_worker_available"],
        matched_capabilities=[],
        matched_roles=[],
        strategy="none",
    )


def compute_retry_delay_seconds(
    attempt: int,
    base_backoff_seconds: float,
    *,
    max_backoff_seconds: float = 30.0,
    jitter_factor: float = 0.2,
) -> float:
    base = max(0.0, float(base_backoff_seconds))
    if base == 0:
        return 0.0
    bounded = min(base * (2 ** max(0, attempt - 1)), max(0.0, float(max_backoff_seconds)))
    jitter = bounded * max(0.0, float(jitter_factor)) * random()
    return bounded + jitter


def build_dispatch_queue(tasks: list[dict]) -> list[dict]:
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    dispatchable = []
    for task in tasks:
        status = str(task.get("status") or "").lower()
        if status not in {"todo", "blocked", "created", "assigned"}:
            continue
        dispatchable.append(task)
    dispatchable.sort(
        key=lambda task: (
            priority_rank.get(str(task.get("priority") or "medium").lower(), 1),
            float(task.get("created_at") or 0.0),
            str(task.get("id") or ""),
        )
    )
    queue = []
    for index, task in enumerate(dispatchable, start=1):
        queue.append(
            {
                "task_id": task.get("id"),
                "priority": task.get("priority"),
                "status": task.get("status"),
                "assigned_agent_url": task.get("assigned_agent_url"),
                "queue_position": index,
            }
        )
    return queue


def _pick_fallback_worker(workers: list[dict], min_security: int) -> dict | None:
    candidates: list[tuple[float, str, dict]] = []
    for worker in workers:
        liveness = dict(worker.get("liveness") or {})
        worker_status = str(worker.get("status") or liveness.get("status") or "").lower()
        if worker.get("available_for_routing") is False or liveness.get("available_for_routing") is False:
            continue
        if worker_status != "online":
            continue
        if worker.get("registration_validated") is False:
            continue
        if _security_level(worker) < min_security:
            continue
        max_parallel = int((worker.get("execution_limits") or {}).get("max_parallel_tasks") or 0)
        current_load = int(worker.get("current_load") or 0)
        if max_parallel > 0 and current_load >= max_parallel:
            continue
        load_ratio = _load_ratio(current_load, max_parallel)
        candidates.append((load_ratio, str(worker.get("url") or ""), worker))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _quality_signals(worker: dict) -> tuple[float, float]:
    routing = dict(worker.get("routing_signals") or {})
    metrics = dict(worker.get("metrics") or {})
    success_rate = _clamp_01(routing.get("success_rate", worker.get("success_rate", metrics.get("success_rate", 0.5))))
    quality_rate = _clamp_01(routing.get("quality_rate", worker.get("quality_rate", metrics.get("quality_rate", success_rate))))
    return success_rate, quality_rate


def _load_ratio(current_load: int, max_parallel: int) -> float:
    if max_parallel > 0:
        return min(max(current_load, 0) / float(max_parallel), 1.0)
    return min(max(current_load, 0) / 4.0, 1.0)


def _security_level(subject: dict) -> int:
    value = str(subject.get("security_level") or subject.get("security_tier") or "medium").strip().lower()
    return SECURITY_LEVEL_RANK.get(value, SECURITY_LEVEL_RANK["medium"])


def _security_label(level: int) -> str:
    for key, value in SECURITY_LEVEL_RANK.items():
        if value == level:
            return key
    return "medium"


def _clamp_01(value: object) -> float:
    try:
        parsed = float(value)
    except Exception:
        return 0.5
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed
