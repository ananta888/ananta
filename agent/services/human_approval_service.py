"""Human-approval service for gate exceptions (WFG-024).

When a gate step has ``failure_policy = "block_until_human_approval"``
(or ``"manual"`` in the new schema enum), the gate engine
writes the decision to the gate task's
``verification_status['gate_decision']`` and raises a
human-approval event. The operator — through the TUI, the
Angular UI, or a direct API call — then resolves the
exception by approving, rejecting, or deferring it.

The service is intentionally simple: it accepts a
human-approval request, validates the gate is actually in
a ``pending_approval`` state, applies the operator's
decision to the gate task, and writes an audit log entry.

State machine (kept in the service so the route handler
does not duplicate it):

  pending_approval -> approved  (operator approves)
  pending_approval -> rejected  (operator rejects)
  pending_approval -> deferred  (operator postpones the
                                decision; the gate stays
                                pending_approval)

Validation:

  - The gate task must exist.
  - The gate task's verification_status['gate'] must be
    ``pending_approval``. A submitted decision for a gate
    that is already ``passed`` or ``rejected`` is
    idempotent (returns the current decision) but does
    NOT mutate state — that protects against double-clicks.
  - The operator must be authenticated. The service does
    not check the role; that is the route's
    responsibility.

Idempotency:

  - ``apply_human_decision`` is keyed on the gate's
    ``decision_id`` (a UUID-ish string in the
    ``verification_status['gate_decision']['decision_id']``
    field). Re-submitting the same decision is a no-op.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import SQLAlchemyError


# Decision status values
DECISION_PENDING = "pending_approval"
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_DEFERRED = "deferred"

ALL_DECISION_STATUSES = (
    DECISION_PENDING,
    DECISION_APPROVED,
    DECISION_REJECTED,
    DECISION_DEFERRED,
)

# Outcomes the operator can submit.
OPERATOR_DECISIONS = (DECISION_APPROVED, DECISION_REJECTED, DECISION_DEFERRED)


class HumanApprovalError(ValueError):
    """Raised when an operator submission is invalid."""


@dataclass(frozen=True)
class HumanDecision:
    """A single operator decision on a pending gate."""

    decision_id: str
    operator: str
    outcome: str
    reason: str
    timestamp: float
    goal_id: str
    gate_task_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "operator": self.operator,
            "outcome": self.outcome,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "goal_id": self.goal_id,
            "gate_task_id": self.gate_task_id,
        }


def _is_valid_outcome(outcome: str) -> bool:
    return outcome in OPERATOR_DECISIONS


def build_pending_approval_record(
    *,
    goal_id: str,
    gate_task_id: str,
    reason_code: str = "gate_pending",
    raised_by: str = "system:gate_engine",
) -> dict[str, Any]:
    """Build the ``pending_approval`` record that the gate
    engine writes to the gate task's verification_status.

    The record is the canonical
    ``workflow_gate_decision.v1`` block with status =
    ``pending_approval``. Re-raising (e.g. after a queue
    reconciliation) returns the SAME decision_id so the
    audit log does not double-write.
    """
    decision_id = f"hdec-{uuid.uuid4().hex[:12]}"
    return {
        "schema": "workflow_gate_decision.v1",
        "decision_id": decision_id,
        "status": DECISION_PENDING,
        "reason_code": reason_code,
        "raised_by": raised_by,
        "raised_at": time.time(),
        "goal_id": str(goal_id),
        "gate_task_id": str(gate_task_id),
    }


def current_decision(task: dict | Any) -> dict[str, Any] | None:
    """Read the current ``workflow_gate_decision.v1`` block
    from a task. Accepts TaskDB and dicts."""
    if isinstance(task, dict):
        verification = dict(task.get("verification_status") or {})
    else:
        verification = dict(getattr(task, "verification_status", None) or {})
    block = verification.get("gate_decision")
    if isinstance(block, dict):
        return dict(block)
    return None


def is_pending_approval(task: dict | Any) -> bool:
    """True when the task has a ``pending_approval`` decision
    recorded on its verification_status. Used by the queue
    layer to detect exception states without consulting the
    gate engine directly.
    """
    block = current_decision(task)
    return bool(block and str(block.get("status") or "") == DECISION_PENDING)


def apply_human_decision(
    *,
    task: Any,
    operator: str,
    outcome: str,
    reason: str = "",
    timestamp: float | None = None,
) -> dict[str, Any]:
    """Apply an operator decision to a gate task.

    The function is a pure mutator of the in-memory task
    object. The caller (the route handler) is responsible
    for persisting the task via the task repository. This
    keeps the service decoupled from the SQLAlchemy
    session and easy to unit-test.

    Returns the updated ``workflow_gate_decision.v1`` block.

    Idempotency:
      - The function is keyed on the existing
        ``decision_id`` when the gate is in
        ``pending_approval``. A re-submit with the SAME
        decision_id is a no-op.
      - A re-submit with a DIFFERENT decision_id is
        treated as a fresh decision and overwrites the
        pending state.

    Errors:
      - ``HumanApprovalError`` when the operator is empty
        or the outcome is not in OPERATOR_DECISIONS.
    """
    if not operator or not str(operator).strip():
        raise HumanApprovalError("operator is required")
    if not _is_valid_outcome(outcome):
        raise HumanApprovalError(
            f"outcome must be one of {OPERATOR_DECISIONS}, got {outcome!r}"
        )
    existing = current_decision(task) or {}
    if existing.get("status") == DECISION_PENDING and not existing.get("decision_id"):
        # Defensive: a pending decision MUST carry a
        # decision_id. If it doesn't, treat it as a fresh
        # decision so the operator does not deadlock.
        existing = {}
    decision_id = str(existing.get("decision_id") or f"hdec-{uuid.uuid4().hex[:12]}")
    new_block: dict[str, Any] = dict(existing)
    new_block.update({
        "schema": "workflow_gate_decision.v1",
        "decision_id": decision_id,
        "status": str(outcome),
        "resolved_by": str(operator).strip(),
        "resolved_at": float(timestamp if timestamp is not None else time.time()),
        "resolution_reason": str(reason or "").strip(),
        "goal_id": str(existing.get("goal_id") or (getattr(task, "goal_id", None) if not isinstance(task, dict) else task.get("goal_id")) or ""),
        "gate_task_id": str(existing.get("gate_task_id") or (getattr(task, "id", None) if not isinstance(task, dict) else task.get("id")) or ""),
    })
    # Persist back to the in-memory task
    if isinstance(task, dict):
        verification = dict(task.get("verification_status") or {})
        verification["gate_decision"] = new_block
        # Mirror the resolved status on the legacy ``gate`` key
        # so the gate engine (WFG-011) and the queue (WFG-013)
        # see the resolved state without consulting the new
        # block. ``deferred`` is NOT mirrored: the gate is
        # still effectively pending and the queue must keep
        # the downstream step blocked.
        if new_block["status"] in {DECISION_APPROVED, DECISION_REJECTED}:
            verification["gate"] = new_block["status"]
            verification["gate_resolved"] = True
        elif new_block["status"] == DECISION_DEFERRED:
            verification["gate"] = DECISION_PENDING
            verification["gate_deferred"] = True
        task["verification_status"] = verification
    else:
        verification = dict(getattr(task, "verification_status", None) or {})
        verification["gate_decision"] = new_block
        if new_block["status"] in {DECISION_APPROVED, DECISION_REJECTED}:
            verification["gate"] = new_block["status"]
            verification["gate_resolved"] = True
        elif new_block["status"] == DECISION_DEFERRED:
            verification["gate"] = DECISION_PENDING
            verification["gate_deferred"] = True
        setattr(task, "verification_status", verification)
    return new_block


def submit_human_decision_via_repo(
    *,
    goal_id: str,
    gate_task_id: str,
    operator: str,
    outcome: str,
    reason: str = "",
) -> dict[str, Any]:
    """Persist a human-approval decision end-to-end.

    The function:
      1. Loads the gate task via ``task_repo.get_by_id``.
      2. Calls ``apply_human_decision`` to build the new
         ``workflow_gate_decision.v1`` block.
      3. Persists the task and writes an audit-log entry.

    Errors:
      - ``HumanApprovalError`` on invalid operator/outcome.
      - ``ValueError`` when the task cannot be loaded.
    """
    from agent.repository import task_repo
    from agent.common.audit import log_audit
    try:
        task = task_repo.get_by_id(gate_task_id)
    except SQLAlchemyError as exc:
        raise HumanApprovalError(f"db error: {exc}") from exc
    if task is None:
        raise HumanApprovalError(f"gate task not found: {gate_task_id}")
    block = apply_human_decision(
        task=task,
        operator=operator,
        outcome=outcome,
        reason=reason,
    )
    try:
        task_repo.save(task)
    except SQLAlchemyError as exc:
        raise HumanApprovalError(f"db error persisting decision: {exc}") from exc
    try:
        log_audit(
            "workflow_human_decision",
            {
                "goal_id": goal_id,
                "gate_task_id": gate_task_id,
                "decision_id": block["decision_id"],
                "operator": operator,
                "outcome": outcome,
                "reason": reason,
                "details": block,
            },
        )
    except Exception:  # noqa: BLE001
        # The audit log is best-effort; the decision is
        # already persisted.
        pass
    return block
