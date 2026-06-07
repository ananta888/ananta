"""Workflow handoff event service (WFG-015).

This module persists the per-step transition events the blueprint
workflow DAG produces. Each materialization, each gate approval, and
each gate rejection writes a structured event so that:

  - the audit query API (WFG-017) can render the full chain,
  - the TUI (WFG-022) and the Angular view (WFG-023) can show
    "PO -> Planner -> Gate -> Developer -> QA -> Final Review" with
    real timestamps,
  - debugging questions like "why is the developer task still
    blocked?" can be answered from the event log.

Storage is intentionally split into two layers so the event log
remains usable even when one side is degraded:

  1. **TaskDB.worker_execution_context["workflow_events"]** — the
     in-task, append-only list. The planner materializer writes
     events here directly when it materializes a task. The
     consumer side reads from here when it needs the full chain
     for one task.

  2. **AuditLogDB via ``log_audit``** — the system-wide
     hash-chained audit log. Every handoff is mirrored to it
     with the action ``workflow_handoff_created`` /
     ``workflow_handoff_released`` / ``workflow_handoff_blocked``
     so the audit view can be re-built from a single source.

Schema:

  The on-wire shape is ``workflow_handoff.v1``::

    {
      "schema": "workflow_handoff.v1",
      "event_id": "<deterministic>",     # dedupe key
      "goal_id": "...",
      "plan_id": "...",
      "workflow_id": "...",
      "from_step": "planner",
      "to_step": "implementation",
      "from_role": "planner",
      "to_role": "developer",
      "task_ids": ["ptask-..."],
      "artifact_refs": ["execution_plan", "task_breakdown"],
      "gate_required": true,
      "gate_task_id": "ptask-...",
      "status": "created" | "released" | "blocked" | "rejected" | "waived",
      "reason_code": "...",
      "timestamp": 1717800000.0,
      "actor": "system" | "agent:<worker>" | "human:<user>",
    }

Secret / credential content is never carried. Only artifact KEYS
(``artifact_refs``), not their payloads.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


WORKFLOW_HANDOFF_SCHEMA = "workflow_handoff.v1"

# Statuses (kept as module-level constants; the strings are the wire
# contract, the dataclass is the in-process shape).
STATUS_CREATED = "created"
STATUS_RELEASED = "released"
STATUS_BLOCKED = "blocked"
STATUS_REJECTED = "rejected"
STATUS_WAIVED = "waived"

ALL_STATUSES = (
    STATUS_CREATED,
    STATUS_RELEASED,
    STATUS_BLOCKED,
    STATUS_REJECTED,
    STATUS_WAIVED,
)


@dataclass(frozen=True)
class HandoffEvent:
    """A single workflow-handoff event.

    Instances are produced by ``build_handoff_event()`` and persisted
    by ``record_handoff_to_task()`` / ``record_handoff_to_audit_log()``.
    The dataclass is frozen so the audit-hash is stable.
    """

    goal_id: str
    plan_id: str
    workflow_id: str
    from_step: str
    to_step: str
    from_role: str
    to_role: str
    task_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    gate_required: bool = False
    gate_task_id: str | None = None
    status: str = STATUS_CREATED
    reason_code: str = ""
    timestamp: float = 0.0
    actor: str = "system"
    blueprint_id: str = ""
    blueprint_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_HANDOFF_SCHEMA,
            "event_id": _event_id_for(self),
            "goal_id": self.goal_id,
            "plan_id": self.plan_id,
            "workflow_id": self.workflow_id,
            "from_step": self.from_step,
            "to_step": self.to_step,
            "from_role": self.from_role,
            "to_role": self.to_role,
            "task_ids": list(self.task_ids),
            "artifact_refs": list(self.artifact_refs),
            "gate_required": bool(self.gate_required),
            "gate_task_id": self.gate_task_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "timestamp": self.timestamp or time.time(),
            "actor": self.actor,
            "blueprint_id": self.blueprint_id,
            "blueprint_version": self.blueprint_version,
        }


def _event_id_for(event: HandoffEvent | dict) -> str:
    """Deterministic dedupe key for a handoff event.

    The id is a SHA-1 of the 5-tuple that uniquely identifies the
    transition: goal/plan/workflow/from/to. Repeated calls with the
    same inputs produce the same id, so a re-materialization does
    not double-write.
    """
    if isinstance(event, HandoffEvent):
        raw = "|".join(
            [
                str(event.goal_id or ""),
                str(event.plan_id or ""),
                str(event.workflow_id or ""),
                str(event.from_step or ""),
                str(event.to_step or ""),
                str(event.status or ""),
            ]
        )
    else:
        raw = "|".join(
            [
                str(event.get("goal_id") or ""),
                str(event.get("plan_id") or ""),
                str(event.get("workflow_id") or ""),
                str(event.get("from_step") or ""),
                str(event.get("to_step") or ""),
                str(event.get("status") or ""),
            ]
        )
    return "hnd-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_handoff_event(
    *,
    goal_id: str,
    plan_id: str,
    workflow_id: str,
    from_step: str,
    to_step: str,
    from_role: str,
    to_role: str,
    task_ids: list[str] | tuple[str, ...] = (),
    artifact_refs: list[str] | tuple[str, ...] = (),
    gate_required: bool = False,
    gate_task_id: str | None = None,
    status: str = STATUS_CREATED,
    reason_code: str = "",
    actor: str = "system",
    blueprint_id: str = "",
    blueprint_version: str = "",
    timestamp: float | None = None,
) -> HandoffEvent:
    """Construct a HandoffEvent with normalized fields.

    Empty / whitespace-only values are dropped; ``status`` is forced
    into the known set (``created``/``released``/``blocked``/
    ``rejected``/``waived``); unknown values are clamped to
    ``created`` so a typo cannot poison the log.
    """
    clean_status = str(status or "").strip().lower()
    if clean_status not in ALL_STATUSES:
        clean_status = STATUS_CREATED
    return HandoffEvent(
        goal_id=str(goal_id or "").strip(),
        plan_id=str(plan_id or "").strip(),
        workflow_id=str(workflow_id or "").strip(),
        from_step=str(from_step or "").strip(),
        to_step=str(to_step or "").strip(),
        from_role=str(from_role or "").strip(),
        to_role=str(to_role or "").strip(),
        task_ids=tuple(str(t) for t in task_ids if str(t).strip()),
        artifact_refs=tuple(str(a) for a in artifact_refs if str(a).strip()),
        gate_required=bool(gate_required),
        gate_task_id=str(gate_task_id).strip() if gate_task_id else None,
        status=clean_status,
        reason_code=str(reason_code or "").strip(),
        timestamp=float(timestamp) if timestamp else time.time(),
        actor=str(actor or "system").strip() or "system",
        blueprint_id=str(blueprint_id or "").strip(),
        blueprint_version=str(blueprint_version or "").strip(),
    )


def append_handoff_to_task(
    *,
    task: dict[str, Any],
    event: HandoffEvent,
) -> dict[str, Any]:
    """Append a handoff event to a task's ``worker_execution_context``.

    Returns a NEW ``worker_execution_context`` dict (the input is
    not mutated). Idempotent: re-appending the same event id is a
    no-op so repeated materialization does not duplicate events.
    """
    if not isinstance(task, dict):
        return {"workflow_events": [event.to_dict()]}
    ctx = dict(task.get("worker_execution_context") or {})
    events = list(ctx.get("workflow_events") or [])
    new_payload = event.to_dict()
    new_id = new_payload.get("event_id")
    if not any(
        isinstance(existing, dict) and existing.get("event_id") == new_id
        for existing in events
    ):
        events.append(new_payload)
    ctx["workflow_events"] = events
    return ctx


def record_handoff_to_audit_log(
    *,
    event: HandoffEvent,
    audit_action_prefix: str = "workflow_handoff",
) -> None:
    """Mirror a handoff event into the system audit log.

    Imports are local to keep the module lightweight for unit tests
    that do not boot the hub. The function is intentionally
    fire-and-forget: if the audit subsystem is degraded the event
    survives in the task's ``worker_execution_context`` and a later
    reconciliation can re-emit it.
    """
    try:
        from agent.common.audit import log_audit
    except Exception:  # noqa: BLE001 — audit subsystem may be optional
        return
    payload = event.to_dict()
    status = payload.get("status") or STATUS_CREATED
    action = f"{audit_action_prefix}_{status}"
    try:
        log_audit(
            action,
            {
                "event_id": payload.get("event_id"),
                "goal_id": payload.get("goal_id"),
                "plan_id": payload.get("plan_id"),
                "task_id": (payload.get("task_ids") or [None])[0],
                "details": payload,
            },
        )
    except Exception:  # noqa: BLE001 — never crash the workflow on audit failure
        return


def _task_context(task: Any) -> dict[str, Any]:
    """Return the ``worker_execution_context`` of a task.

    Accepts both ``TaskDB`` (uses attribute access) and plain dicts
    (uses key access). Returns ``{}`` for anything else so the
    downstream sort / filter does not crash.
    """
    if isinstance(task, dict):
        return dict(task.get("worker_execution_context") or {})
    return dict(getattr(task, "worker_execution_context", None) or {})


def list_handoffs_for_task(task: dict | Any) -> list[dict[str, Any]]:
    """Return the append-only handoff-event list for a task.

    Sorted by ``timestamp`` ascending so consumers can render the
    chain without re-sorting.
    """
    ctx = _task_context(task)
    events = list(ctx.get("workflow_events") or [])
    events.sort(key=lambda e: float(e.get("timestamp") or 0.0))
    return events


def list_handoffs_for_goal(
    *, tasks: list[dict | Any]
) -> list[dict[str, Any]]:
    """Aggregate handoff events across all tasks for a goal.

    Inputs are the list of ``TaskDB`` instances (or plain dicts)
    that share a ``goal_id``. The output is the merged, time-sorted
    list of all handoff events. The audit query (WFG-017) uses this
    to render the full chain in one response.
    """
    collected: list[dict[str, Any]] = []
    for task in tasks:
        collected.extend(list_handoffs_for_task(task))
    collected.sort(key=lambda e: float(e.get("timestamp") or 0.0))
    return collected


def latest_handoff_for_pair(
    *, tasks: list[dict | Any], from_step: str, to_step: str
) -> dict[str, Any] | None:
    """Return the most recent handoff between two specific steps.

    Used by the queue layer to ask "what's the current status of the
    handoff from planner to developer?" without re-scanning the
    whole list.
    """
    from_clean = str(from_step or "").strip()
    to_clean = str(to_step or "").strip()
    if not from_clean or not to_clean:
        return None
    candidates = [
        e for e in list_handoffs_for_goal(tasks=tasks)
        if e.get("from_step") == from_clean and e.get("to_step") == to_clean
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda e: float(e.get("timestamp") or 0.0))
    return candidates[-1]


# ---------------------------------------------------------------------------
# Convenience: derive handoff events from a list of (normalized) workflow
# steps during materialization. This is what the planning track integrator
# uses to fan-out the chain in one pass.
# ---------------------------------------------------------------------------


def derive_handoffs_for_steps(
    *,
    goal_id: str,
    plan_id: str,
    workflow_id: str,
    steps: list[dict[str, Any]],
    task_id_by_step: dict[str, str],
    blueprint_id: str = "",
    blueprint_version: str = "",
    artifact_refs_by_step: dict[str, list[str]] | None = None,
    timestamp: float | None = None,
) -> list[HandoffEvent]:
    """Compute the handoff events for a fully materializable workflow.

    Given a topologically ordered list of workflow steps, the helper
    walks the DAG and emits one ``HandoffEvent`` per edge. Gate edges
    get a separate event with ``gate_required=True`` so the audit
    query (WFG-017) can highlight them.

    The output is ordered: the first event is the entry-point handoff
    (no ``from_step``, derived from the step with no ``depends_on``),
    then the chain follows the topological order. Idempotent: a step
    with no ``task_id`` is skipped so a half-materialized plan does
    not produce dangling events.
    """
    artifact_refs_by_step = dict(artifact_refs_by_step or {})
    events: list[HandoffEvent] = []
    if not isinstance(steps, list):
        return events
    # Index by id for the edge walk
    by_id: dict[str, dict[str, Any]] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "").strip()
        if step_id:
            by_id[step_id] = step
    seen_pair: set[tuple[str, str]] = set()
    for step_id, step in by_id.items():
        to_task = str(task_id_by_step.get(step_id) or "").strip()
        if not to_task:
            continue
        deps = [
            str(d).strip() for d in list(step.get("depends_on") or [])
            if str(d).strip()
        ]
        if not deps:
            # Entry point: synthesize a handoff from "intake" (no real
            # step) so the chain has a start.
            events.append(
                build_handoff_event(
                    goal_id=goal_id,
                    plan_id=plan_id,
                    workflow_id=workflow_id,
                    from_step="intake",
                    to_step=step_id,
                    from_role="intake",
                    to_role=str(step.get("role") or "").strip(),
                    task_ids=[to_task],
                    artifact_refs=artifact_refs_by_step.get(step_id, []),
                    gate_required=bool(step.get("gate", False)),
                    gate_task_id=to_task if step.get("gate") else None,
                    status=STATUS_CREATED,
                    blueprint_id=blueprint_id,
                    blueprint_version=blueprint_version,
                    timestamp=timestamp,
                )
            )
            seen_pair.add(("intake", step_id))
            continue
        for dep in deps:
            if (dep, step_id) in seen_pair:
                continue
            dep_step = by_id.get(dep) or {}
            gate_required = bool(step.get("gate", False)) or bool(dep_step.get("gate", False))
            # If a gate sits between the dep and this step, the gate's
            # task_id is used. The dep is treated as released; the
            # gate task is the blocker.
            gate_task_id = None
            if step.get("gate"):
                gate_task_id = to_task
            elif dep_step.get("gate"):
                gate_task_id = str(task_id_by_step.get(dep) or "").strip() or None
            events.append(
                build_handoff_event(
                    goal_id=goal_id,
                    plan_id=plan_id,
                    workflow_id=workflow_id,
                    from_step=dep,
                    to_step=step_id,
                    from_role=str(dep_step.get("role") or "").strip(),
                    to_role=str(step.get("role") or "").strip(),
                    task_ids=[to_task],
                    artifact_refs=artifact_refs_by_step.get(step_id, []),
                    gate_required=bool(gate_required),
                    gate_task_id=gate_task_id,
                    status=STATUS_CREATED,
                    blueprint_id=blueprint_id,
                    blueprint_version=blueprint_version,
                    timestamp=timestamp,
                )
            )
            seen_pair.add((dep, step_id))
    return events
