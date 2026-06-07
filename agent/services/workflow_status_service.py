"""Workflow audit / status service (WFG-017).

The audit query is the read-only mirror of the workflow write path
(WFG-006 adapter → WFG-007 materializer → WFG-015 handoff events →
WFG-011 gate engine → WFG-012 decision model). The audit service
answers three questions without round-tripping through the queue:

  1. "What is the current state of the workflow for a given
     goal?" — ``build_workflow_status(goal_id)`` returns steps,
     tasks, gate decisions, blocking reasons, artifact_refs,
     and the handoff-event chain in a single response.

  2. "Why is the developer task still blocked?" — the same
     function includes a per-task ``blocker_summary`` that
     names the gating dependency (gate decision, missing
     artifact, stale gate) and the chain of handoff events
     leading to the block.

  3. "Is the response safe to send to a user?" — the function
     redacts known-sensitive keys (the existing
     ``common.redaction`` module) and never returns raw
     artifact payloads, only KEYS.

The service is intentionally pure: it does not modify any
state, it only reads from the task repository. This keeps it
test-friendly (no DB fixtures) and lets the same shape be
served from a cached / materialized read model in the future.

The output schema is ``workflow_status.v1``::

  {
    "schema": "workflow_status.v1",
    "goal_id": "...",
    "plan_id": "...",
    "blueprint_id": "...",
    "blueprint_version": "...",
    "steps": [
      {
        "step_id": "...",
        "role": "...",
        "task_kind": "...",
        "task_id": "...",
        "task_status": "...",
        "task_blocker_reason": "...",
        "gate": true,
        "gate_decision": "...",
        "blocked_reasons": [...],
        "missing_consumes": [...],
        "is_blocker": true,
      },
      ...
    ],
    "handoff_events": [...],
    "audit_log_actions": [...],
  }

Reuse: the response is the same shape the TUI (WFG-022) and
the Angular view (WFG-023) render. Adding a new visualization
client does not require re-implementing the audit query.
"""

from __future__ import annotations

from typing import Any

from agent.common.redaction import DEFAULT_SENSITIVE_KEYS, VisibilityLevel, redact
from agent.services.workflow_event_service import list_handoffs_for_goal


WORKFLOW_STATUS_SCHEMA = "workflow_status.v1"

# Status values that mean "this task is currently blocked on a
# gate / artifact / dependency". Kept module-level so the route
# can use the same constants when computing the HTTP status code.
GATING_STATUSES = {"blocked", "pending_approval", "missing_artifacts"}


def _redact(value: Any) -> Any:
    """Apply the project-wide sensitive-key redactor.

    The audit endpoint must NEVER leak credentials, tokens, or
    raw artifact payloads. We use the user-level redaction so
    the response is safe to render in the TUI / Angular UI.
    """
    return redact(value, VisibilityLevel.USER)


def _normalize_step_id(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("id") or step.get("step_id") or "").strip()
    return str(getattr(step, "id", "") or getattr(step, "step_id", "") or "").strip()


def _step_role(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("role") or "").strip()
    return str(getattr(step, "role", "") or "").strip()


def _step_task_kind(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("task_kind") or "").strip()
    return str(getattr(step, "task_kind", "") or "").strip()


def _step_gate(step: Any) -> bool:
    if isinstance(step, dict):
        return bool(step.get("gate", False))
    return bool(getattr(step, "gate", False))


def _step_consumes(step: Any) -> list[str]:
    if isinstance(step, dict):
        items = list(step.get("consumes") or [])
    else:
        items = list(getattr(step, "consumes", None) or [])
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(item.strip())
        elif isinstance(item, dict):
            out.append(str(item.get("key") or "").strip())
    return [k for k in out if k]


def _task_blocker_reason(task: Any) -> str:
    """The reason_code that explains why a task is blocked.

    Reads from the task's ``status_reason_code`` (WFG-012 contract)
    and falls back to ``status_reason_details.gate_pending_since``
    or ``status_reason_details.missing_artifacts`` (WFG-016 contract).
    Returns an empty string when the task is not blocked.
    """
    if isinstance(task, dict):
        status = str(task.get("status") or "").strip()
        if status not in {"blocked", "pending_approval"}:
            return ""
        reason_code = str(task.get("status_reason_code") or "").strip()
        if reason_code:
            return reason_code
        details = dict(task.get("status_reason_details") or {})
        if "missing_artifacts" in details:
            return "missing_artifacts"
        if "gate_pending_since" in details:
            return "gate_pending"
        return "blocked"
    status = str(getattr(task, "status", "") or "").strip()
    if status not in {"blocked", "pending_approval"}:
        return ""
    reason_code = str(getattr(task, "status_reason_code", "") or "").strip()
    if reason_code:
        return reason_code
    details = dict(getattr(task, "status_reason_details", None) or {})
    if "missing_artifacts" in details:
        return "missing_artifacts"
    if "gate_pending_since" in details:
        return "gate_pending"
    return "blocked"


def _task_gate_decision(task: Any) -> str:
    """Read the gate decision recorded on the task's
    ``verification_status`` (WFG-012 contract). Returns an empty
    string when the task has no gate decision yet.
    """
    if isinstance(task, dict):
        verification = dict(task.get("verification_status") or {})
    else:
        verification = dict(getattr(task, "verification_status", None) or {})
    block = verification.get("gate")
    if block in {"passed", "skipped", "failed", "pending_approval", "stale"}:
        return str(block)
    decision = verification.get("gate_decision")
    if isinstance(decision, dict):
        return str(decision.get("status") or "").strip()
    return ""


def _workflow_block_for_task(task: Any) -> dict[str, Any] | None:
    """Read the WFG-012 ``workflow_gate_decision.v1`` block if the
    task has one persisted on it. Returns None when the task is
    not gated or has not been evaluated yet.
    """
    if isinstance(task, dict):
        verification = dict(task.get("verification_status") or {})
    else:
        verification = dict(getattr(task, "verification_status", None) or {})
    block = verification.get("gate_decision")
    if isinstance(block, dict):
        return dict(block)
    return None


def _step_status_summary(
    *, step_id: str, role: str, task_kind: str, gate: bool, task: Any
) -> dict[str, Any]:
    """Compose a single step's status block (no redaction yet).

    The caller applies redaction on the whole response before
    returning it to the client.
    """
    task_id = ""
    task_status = ""
    if task is not None:
        if isinstance(task, dict):
            task_id = str(task.get("id") or "").strip()
            task_status = str(task.get("status") or "").strip()
        else:
            task_id = str(getattr(task, "id", "") or "").strip()
            task_status = str(getattr(task, "status", "") or "").strip()
    blocker = _task_blocker_reason(task) if task is not None else ""
    gate_decision = _task_gate_decision(task) if task is not None else ""
    return {
        "step_id": step_id,
        "role": role,
        "task_kind": task_kind,
        "task_id": task_id,
        "task_status": task_status,
        "task_blocker_reason": blocker,
        "gate": bool(gate),
        "gate_decision": gate_decision,
        # The caller (build_workflow_status) appends extra
        # reasons (not_materialised, missing_artifacts,
        # workflow_gate_decision reason_code) to the
        # ``blocked_reasons`` list. We report the pre-summary
        # state here; build_workflow_status overlays the final
        # ``is_blocker`` from the joined list so the user
        # never sees a "not blocked" task that actually has a
        # reason.
        "is_blocker": bool(blocker or task_status in GATING_STATUSES),
        "_pending_blocked_reasons": [],
    }


def _audit_log_actions(goal_id: str) -> list[str]:
    """Best-effort fetch of the system audit-log action names
    that mention the given goal. Pure function: any failure is
    swallowed because the audit subsystem is optional from the
    audit query's point of view.
    """
    try:
        from agent.common.audit import _engine  # type: ignore
        from sqlmodel import Session, select
        from agent.db_models import AuditLogDB
        with Session(_engine()) as session:
            rows = session.exec(
                select(AuditLogDB.action)
                .where(AuditLogDB.goal_id == goal_id)
                .order_by(AuditLogDB.id.asc())
            ).all()
        return [str(a) for a in rows if a]
    except Exception:  # noqa: BLE001
        return []


def build_workflow_status(
    *,
    goal_id: str,
    workflow_id: str = "",
    blueprint_id: str = "",
    blueprint_version: str = "",
    steps: list[Any] | None = None,
    tasks: list[Any] | None = None,
    produced_artifact_keys: list[str] | tuple[str, ...] | None = None,
    plan_id: str = "",
    include_audit_log: bool = True,
    debug_summary: bool = False,
) -> dict[str, Any]:
    """Build the full ``workflow_status.v1`` response for one goal.

    Parameters mirror the inputs the planner materializer and the
    workflow definition service already have, so the route
    handler can pass them in without re-querying.

    The function is pure: it does not write to the DB. The
    ``produced_artifact_keys`` argument comes from the goal
    artifact graph (the planner knows which artifacts are
    available when this step is evaluated).
    """
    step_list = list(steps or [])
    task_list = list(tasks or [])
    # Build a step_id -> task mapping. The materializer uses
    # ``plan_node_id`` as the join key; we look for a
    # ``workflow_step.step_id`` on the task context as a backup
    # for older materialised tasks.
    task_by_step: dict[str, Any] = {}
    for task in task_list:
        plan_node_id = ""
        if isinstance(task, dict):
            plan_node_id = str(task.get("plan_node_id") or "").strip()
        else:
            plan_node_id = str(getattr(task, "plan_node_id", "") or "").strip()
        if plan_node_id:
            task_by_step[plan_node_id] = task
            continue
        # Fallback: look in worker_execution_context
        ctx = (
            dict(task.get("worker_execution_context") or {})
            if isinstance(task, dict)
            else dict(getattr(task, "worker_execution_context", None) or {})
        )
        ws = ctx.get("workflow_step") if isinstance(ctx, dict) else None
        if isinstance(ws, dict):
            step_id = str(ws.get("step_id") or "").strip()
            if step_id:
                task_by_step[step_id] = task
    steps_out: list[dict[str, Any]] = []
    for step in step_list:
        step_id = _normalize_step_id(step)
        task = task_by_step.get(step_id)
        summary = _step_status_summary(
            step_id=step_id,
            role=_step_role(step),
            task_kind=_step_task_kind(step),
            gate=_step_gate(step),
            task=task,
        )
        # Blocked-reason details: the gate decision block (WFG-012)
        # plus the missing-consumes list (WFG-016).
        blocked_reasons: list[str] = []
        if task is not None and summary["task_blocker_reason"]:
            blocked_reasons.append(summary["task_blocker_reason"])
        if task is not None:
            decision_block = _workflow_block_for_task(task)
            if isinstance(decision_block, dict):
                reason_code = str(decision_block.get("reason_code") or "").strip()
                if reason_code:
                    blocked_reasons.append(reason_code)
        if task is None:
            # Step declared in the workflow but not yet
            # materialised into a task — visible as a blocker so
            # the audit query can answer "why isn't this
            # developer task running yet?".
            blocked_reasons.append("not_materialised")
        # Missing consumes (WFG-016)
        consumes = _step_consumes(step)
        produced = {
            str(k).strip()
            for k in list(produced_artifact_keys or [])
            if str(k).strip()
        }
        missing_consumes = [c for c in consumes if c not in produced]
        if missing_consumes:
            blocked_reasons.append("missing_artifacts")
        summary["blocked_reasons"] = blocked_reasons
        summary["missing_consumes"] = missing_consumes
        summary["is_blocker"] = bool(
            blocked_reasons or summary["is_blocker"]
        )
        steps_out.append(summary)
    # Tasks that have workflow-step provenance but are not in
    # ``steps`` (e.g. a half-materialised workflow). Surface them
    # as "orphan" steps so the user can see them.
    seen_step_ids = {_normalize_step_id(s) for s in step_list}
    for step_id, task in task_by_step.items():
        if step_id in seen_step_ids:
            continue
        ws = (
            dict(task.get("worker_execution_context") or {}).get("workflow_step")
            if isinstance(task, dict)
            else dict(getattr(task, "worker_execution_context", None) or {}).get(
                "workflow_step"
            )
        )
        if not isinstance(ws, dict):
            continue
        summary = _step_status_summary(
            step_id=step_id,
            role=str(ws.get("role") or "").strip(),
            task_kind=str(ws.get("task_kind") or "").strip(),
            gate=bool(ws.get("gate", False)),
            task=task,
        )
        blocked_reasons = (
            [summary["task_blocker_reason"]] if summary["task_blocker_reason"] else []
        )
        summary["blocked_reasons"] = blocked_reasons
        summary["missing_consumes"] = []
        summary["is_blocker"] = bool(blocked_reasons or summary["is_blocker"])
        steps_out.append(summary)
    # Handoff events: aggregate across the goal's tasks.
    handoff_events = list_handoffs_for_goal(tasks=task_list)
    audit_actions = (
        _audit_log_actions(goal_id) if include_audit_log else []
    )
    response: dict[str, Any] = {
        "schema": WORKFLOW_STATUS_SCHEMA,
        "goal_id": str(goal_id or "").strip(),
        "plan_id": str(plan_id or "").strip(),
        "workflow_id": str(workflow_id or "").strip(),
        "blueprint_id": str(blueprint_id or "").strip(),
        "blueprint_version": str(blueprint_version or "").strip(),
        "steps": steps_out,
        "handoff_events": handoff_events,
        "audit_log_actions": audit_actions,
    }
    if debug_summary:
        # Compact human-readable summary. The TUI renders this
        # when the user runs ":workflow status <goal_id>".
        blocking = [s for s in steps_out if s["is_blocker"]]
        response["debug_summary"] = {
            "step_count": len(steps_out),
            "blocking_count": len(blocking),
            "blocking_steps": [
                {
                    "step_id": s["step_id"],
                    "role": s["role"],
                    "task_id": s["task_id"],
                    "reason": s["task_blocker_reason"] or "blocked",
                    "blocked_reasons": s["blocked_reasons"],
                }
                for s in blocking
            ],
        }
    return _redact(response) if isinstance(_redact(response), dict) else response


def debug_workflow_status(
    *,
    goal_id: str,
    workflow_id: str = "",
    blueprint_id: str = "",
    blueprint_version: str = "",
    steps: list[Any] | None = None,
    tasks: list[Any] | None = None,
    produced_artifact_keys: list[str] | tuple[str, ...] | None = None,
    plan_id: str = "",
) -> str:
    """Render a compact, multi-line debug summary of a workflow.

    The TUI (WFG-022) prints this when the user asks for
    ":workflow status <goal_id>". Pure function: no I/O.
    """
    status = build_workflow_status(
        goal_id=goal_id,
        workflow_id=workflow_id,
        blueprint_id=blueprint_id,
        blueprint_version=blueprint_version,
        steps=steps,
        tasks=tasks,
        produced_artifact_keys=produced_artifact_keys,
        plan_id=plan_id,
        include_audit_log=False,
        debug_summary=True,
    )
    lines: list[str] = []
    lines.append(f"Workflow status for goal {status['goal_id']}:")
    if status.get("plan_id"):
        lines.append(f"  plan_id: {status['plan_id']}")
    if status.get("blueprint_id"):
        lines.append(f"  blueprint: {status['blueprint_id']}@v{status.get('blueprint_version', '')}")
    lines.append(f"  steps: {len(status['steps'])}")
    blocking = [
        s for s in status["steps"] if s.get("is_blocker")
    ]
    if not blocking:
        lines.append("  no blocking steps.")
    else:
        lines.append(f"  {len(blocking)} step(s) blocked:")
        for s in blocking:
            tid = s.get("task_id") or "<no task>"
            reason = s.get("task_blocker_reason") or "blocked"
            lines.append(f"    - [{s['step_id']}] {s['role']} task={tid} -> {reason}")
            for extra in s.get("blocked_reasons", []):
                if extra != reason:
                    lines.append(f"        reason: {extra}")
    return "\n".join(lines)
