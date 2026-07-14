"""Fail-closed ownership and idempotency primitives for Hub chat sessions.

The chat routes still use the existing project-local ``UserConfigManager`` as
their persistence adapter.  This module owns only the security rules applied
at that boundary: exact principal matching, deterministic legacy migration and
scope-bound gate command fingerprints.  It deliberately performs no routing
or workflow execution.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from agent.services.identity_validation import require_canonical_identity

SESSION_OWNER_FIELD = "owner_principal"
MAX_GATE_IDEMPOTENCY_KEY_LENGTH = 200
MAX_GATE_STEP_ID_LENGTH = 160
GATE_PENDING_RECONCILE_AFTER_SECONDS = 300.0

# The Hub currently serves chat state from one project-local JSON document and
# Flask runs it in threaded mode.  Every route that mutates that document uses
# this lock, turning load/authorize/change/save into one in-process transaction.
# Containers do not share this state, in accordance with Ananta's container
# ownership model.
chat_session_mutation_lock = threading.RLock()


@dataclass(frozen=True)
class ChatSessionPrincipal:
    tenant_id: str
    subject_id: str

    @classmethod
    def from_values(cls, tenant_id: Any, subject_id: Any) -> "ChatSessionPrincipal":
        return cls(
            tenant_id=require_canonical_identity(tenant_id, field_name="tenant_id"),
            subject_id=require_canonical_identity(subject_id, field_name="subject_id"),
        )

    def to_dict(self) -> dict[str, str]:
        return {"tenant_id": self.tenant_id, "subject_id": self.subject_id}

    @property
    def storage_key(self) -> str:
        payload = f"{self.tenant_id}\0{self.subject_id}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def session_owner(session: dict[str, Any]) -> ChatSessionPrincipal | None:
    raw = session.get(SESSION_OWNER_FIELD)
    if not isinstance(raw, dict):
        return None
    try:
        return ChatSessionPrincipal.from_values(raw.get("tenant_id"), raw.get("subject_id"))
    except ValueError:
        return None


def inferred_legacy_owner(
    session: dict[str, Any],
    *,
    default_owner: ChatSessionPrincipal | None,
) -> ChatSessionPrincipal | None:
    """Infer old ownership without allowing the first caller to claim data.

    A session containing workflow runs is migratable only when every run has
    one identical, canonical control principal.  Older sessions without runs
    belong to the configured initial Hub administrator.  Ambiguous or damaged
    records remain inaccessible and require an explicit offline migration.
    """

    raw_runs = session.get("process_runs")
    runs = raw_runs if isinstance(raw_runs, list) else []
    if not runs:
        return default_owner
    owners: set[ChatSessionPrincipal] = set()
    for run in runs:
        if not isinstance(run, dict):
            return None
        raw = run.get("control_principal")
        if not isinstance(raw, dict):
            return None
        try:
            owners.add(ChatSessionPrincipal.from_values(raw.get("tenant_id"), raw.get("subject_id")))
        except ValueError:
            return None
    return next(iter(owners)) if len(owners) == 1 else None


def authorize_owned_record(
    record: dict[str, Any],
    principal: ChatSessionPrincipal,
    *,
    legacy_default_owner: ChatSessionPrincipal | None,
) -> tuple[bool, bool]:
    """Return ``(authorized, migrated)`` using exact tenant and subject IDs."""

    owner = session_owner(record)
    if owner is not None:
        return owner == principal, False
    # A present but malformed owner is damaged security metadata, not a
    # claimable legacy record.  Only records which genuinely predate the
    # owner field may use the deterministic legacy migration below.
    if SESSION_OWNER_FIELD in record:
        return False, False
    inferred = inferred_legacy_owner(record, default_owner=legacy_default_owner)
    if inferred != principal:
        return False, False
    record[SESSION_OWNER_FIELD] = principal.to_dict()
    return True, True


def authorize_session(
    session: dict[str, Any],
    principal: ChatSessionPrincipal,
    *,
    legacy_default_owner: ChatSessionPrincipal | None,
) -> tuple[bool, bool]:
    return authorize_owned_record(
        session,
        principal,
        legacy_default_owner=legacy_default_owner,
    )


def public_owned_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return the established API shape without exposing persistence metadata."""

    result = dict(record)
    result.pop(SESSION_OWNER_FIELD, None)
    return result


def public_session(session: dict[str, Any]) -> dict[str, Any]:
    return public_owned_record(session)


@dataclass(frozen=True)
class GateCommand:
    idempotency_key: str
    tenant_id: str
    subject_id: str
    session_id: str
    workflow_id: str
    run_id: str
    step_id: str
    decision: str

    @classmethod
    def from_values(
        cls,
        *,
        idempotency_key: Any,
        principal: ChatSessionPrincipal,
        session_id: str,
        workflow_id: str,
        run_id: str,
        step_id: Any,
        decision: Any,
    ) -> "GateCommand":
        if not isinstance(idempotency_key, str):
            raise ValueError("idempotency_key_invalid")
        key = idempotency_key
        if not key:
            raise ValueError("idempotency_key_required")
        if (
            key != key.strip()
            or len(key) > MAX_GATE_IDEMPOTENCY_KEY_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in key)
        ):
            raise ValueError("idempotency_key_invalid")
        if not isinstance(step_id, str):
            raise ValueError("gate_step_id_invalid")
        canonical_step = step_id
        if (
            not canonical_step
            or canonical_step != canonical_step.strip()
            or len(canonical_step) > MAX_GATE_STEP_ID_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in canonical_step)
        ):
            raise ValueError("gate_step_id_invalid")
        if not isinstance(decision, str) or decision not in {"approve", "reject"}:
            raise ValueError("invalid_gate_decision")
        return cls(
            idempotency_key=key,
            tenant_id=principal.tenant_id,
            subject_id=principal.subject_id,
            session_id=session_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=canonical_step,
            decision=decision,
        )

    @property
    def request_hash(self) -> str:
        payload = {
            "schema": "ananta.chat_gate_command.v1",
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "actor": self.subject_id,
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "decision": self.decision,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def idempotency_key_ref(self) -> str:
        """Return an opaque observability reference, never the caller key."""

        return f"idempotency-sha256:{hashlib.sha256(self.idempotency_key.encode('utf-8')).hexdigest()}"

    def matches_scope(self, action: dict[str, Any]) -> bool:
        return all(
            action.get(key) == value
            for key, value in (
                ("tenant_id", self.tenant_id),
                ("subject_id", self.subject_id),
                ("session_id", self.session_id),
                ("workflow_id", self.workflow_id),
                ("run_id", self.run_id),
            )
        )

    def action(self, *, state: str, created_at: float) -> dict[str, Any]:
        return {
            "schema": "ananta.chat_gate_action.v1",
            "idempotency_key": self.idempotency_key,
            "request_hash": self.request_hash,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "actor": self.subject_id,
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "decision": self.decision,
            "state": state,
            "created_at": created_at,
            "updated_at": created_at,
            "reconcile_after": created_at + GATE_PENDING_RECONCILE_AFTER_SECONDS,
        }


def find_gate_action(actions: Iterable[Any], command: GateCommand) -> dict[str, Any] | None:
    """Find a replay only inside the command's exact principal/run scope."""

    for item in actions:
        if not isinstance(item, dict):
            continue
        if item.get("idempotency_key") != command.idempotency_key:
            continue
        if command.matches_scope(item):
            return item
    return None


def mark_stale_gate_action_for_manual_reconciliation(
    action: dict[str, Any],
    *,
    now: float,
) -> bool:
    """Expire an abandoned reservation without ever replaying its signal.

    A persisted ``pending`` row proves only that the external side effect may
    have happened.  Retrying it automatically is therefore unsafe.  Once the
    reconciliation deadline passes, the row becomes a terminal, operator
    visible state and remains non-replayable.
    """

    if action.get("state") != "pending":
        return False
    try:
        reconcile_after = float(action.get("reconcile_after"))
    except (TypeError, ValueError):
        try:
            reconcile_after = float(action.get("created_at")) + GATE_PENDING_RECONCILE_AFTER_SECONDS
        except (TypeError, ValueError):
            # Corrupt timestamps also fail closed; they cannot justify another
            # external signal.
            reconcile_after = 0.0
    if now < reconcile_after:
        return False
    action["state"] = "manual_reconcile_required"
    action["error_code"] = "gate_signal_outcome_unknown"
    action["updated_at"] = now
    return True


__all__ = [
    "ChatSessionPrincipal",
    "GATE_PENDING_RECONCILE_AFTER_SECONDS",
    "GateCommand",
    "SESSION_OWNER_FIELD",
    "authorize_owned_record",
    "authorize_session",
    "chat_session_mutation_lock",
    "find_gate_action",
    "mark_stale_gate_action_for_manual_reconciliation",
    "public_owned_record",
    "public_session",
    "session_owner",
]
