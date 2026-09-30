"""Row mapping, fencing and input rules shared by the dispatch-intent stores.

Pure functions used by both the in-memory and the SQL adapter of the Hub
workflow-control dispatch outbox; they never open sessions or mutate state.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

import sqlalchemy as sa

from agent.db_models.workflow_runtime import (
    WorkflowControlBindingDB,
    WorkflowControlDispatchIntentDB,
)
from agent.services.workflow_control_bindings import (
    WorkflowControlRunBinding,
)
from agent.services.workflow_control_dispatch_intents import (
    DISPATCH_KIND_START,
    DISPATCH_STATE_COMPLETED,
    DISPATCH_STATE_DISPATCHING,
    DISPATCH_STATE_OBSERVATION_PENDING,
    DISPATCH_STATE_READY,
    DISPATCH_STATE_REJECTED,
    WorkflowControlDispatchIntent,
    WorkflowControlDispatchIntentError,
)


def _intent(row: WorkflowControlDispatchIntentDB) -> WorkflowControlDispatchIntent:
    return WorkflowControlDispatchIntent(
        intent_id=str(row.id),
        kind=str(row.kind),
        tenant_id=str(row.tenant_id),
        workflow_id=str(row.workflow_id),
        run_id=str(row.run_id),
        payload=deepcopy(dict(row.payload)),
        state=str(row.state),
        dispatch_from_state=str(row.dispatch_from_state),
        acknowledgement_revision=int(row.acknowledgement_revision),
        acknowledgement_status=str(row.acknowledgement_status),
        attempt_count=int(row.attempt_count),
        available_at=float(row.available_at),
        lease_owner=str(row.lease_owner),
        lease_expires_at=float(row.lease_expires_at),
        last_error=str(row.last_error),
        revision=int(row.revision),
    )


def _assert_binding_row(
    row: WorkflowControlBindingDB | None,
    binding: WorkflowControlRunBinding,
) -> None:
    if row is None or any(
        (
            str(row.tenant_id) != binding.tenant_id,
            str(row.workflow_id) != binding.workflow_id,
            str(row.run_id) != binding.run_id,
            str(row.plan_hash) != binding.plan_hash,
            str(row.policy_version) != binding.policy_version,
            str(row.runtime_id) != binding.runtime_id,
        )
    ):
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_binding_mismatch")


def _claimable(
    row: WorkflowControlDispatchIntent,
    *,
    now: float,
    owner_id: str,
) -> bool:
    del owner_id
    if row.available_at > now or row.state in {
        DISPATCH_STATE_COMPLETED,
        DISPATCH_STATE_REJECTED,
    }:
        return False
    if row.state in {DISPATCH_STATE_READY, DISPATCH_STATE_OBSERVATION_PENDING}:
        return True
    return row.state == DISPATCH_STATE_DISPATCHING and row.lease_expires_at <= now


def _claimable_sql(*, now: float, owner_id: str) -> Any:
    del owner_id
    return sa.or_(
        WorkflowControlDispatchIntentDB.state.in_([DISPATCH_STATE_READY, DISPATCH_STATE_OBSERVATION_PENDING]),
        sa.and_(
            WorkflowControlDispatchIntentDB.state == DISPATCH_STATE_DISPATCHING,
            WorkflowControlDispatchIntentDB.lease_expires_at <= now,
        ),
    )


def _owned(row: WorkflowControlDispatchIntentDB | None, owner_id: str) -> bool:
    return bool(row is not None and row.state == DISPATCH_STATE_DISPATCHING and row.lease_owner == str(owner_id))


def _assert_ack_fence(
    intent: WorkflowControlDispatchIntentDB,
    status: dict[str, Any],
) -> None:
    minimum = int(intent.acknowledgement_revision or 0)
    expected = str(intent.acknowledgement_status or "")
    if minimum < 1:
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_acknowledgement_missing")
    source = status.get("source_observation")
    revision = source.get("revision") if isinstance(source, dict) else None
    source_status = source.get("status") if isinstance(source, dict) else None
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < minimum
        or (revision == minimum and expected and source_status != expected)
    ):
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_observation_fence_conflict")


def _status_revision(status: dict[str, Any]) -> int:
    revision = status.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_status_revision_invalid")
    return revision


def _status_checkpoint(status: dict[str, Any], *, fallback: str) -> str:
    value = status.get("checkpoint_ref") or fallback
    if not isinstance(value, str) or not value or len(value) > 512:
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_status_checkpoint_invalid")
    return value


def _ack_revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_acknowledgement_revision_invalid")
    return value


def _ack_status(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 64:
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_acknowledgement_status_invalid")
    return value


def _lease_seconds(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_lease_invalid") from exc
    if parsed <= 0:
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_lease_invalid")
    return min(parsed, 300.0)


def _reason(value: Any) -> str:
    normalized = str(value or "").strip()[:256]
    if not normalized or any(not character.isprintable() for character in normalized):
        return "workflow_control_dispatch_retry_pending"
    return normalized


def _start_intent_id(workflow_id: str) -> str:
    normalized = str(workflow_id or "").strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"start:{digest}"


def _assert_exact_start_intent(
    intent: WorkflowControlDispatchIntent,
    *,
    binding: WorkflowControlRunBinding,
    payload: dict[str, Any],
) -> WorkflowControlDispatchIntent:
    if (
        intent.kind != DISPATCH_KIND_START
        or intent.tenant_id != binding.tenant_id
        or intent.workflow_id != binding.workflow_id
        or intent.run_id != binding.run_id
        or intent.payload != payload
    ):
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_conflict")
    return intent
