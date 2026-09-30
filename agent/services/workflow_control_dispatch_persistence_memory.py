"""Process-local dispatch-intent store for tests and local-only composition."""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from dataclasses import replace
from typing import Any

from agent.services.workflow_control_bindings import (
    InMemoryWorkflowControlBindingStore,
    WorkflowControlRunBinding,
)
from agent.services.workflow_control_dispatch_intents import (
    DISPATCH_KIND_COMMAND,
    DISPATCH_KIND_START,
    DISPATCH_STATE_COMPLETED,
    DISPATCH_STATE_DISPATCHING,
    DISPATCH_STATE_OBSERVATION_PENDING,
    DISPATCH_STATE_REJECTED,
    WorkflowControlDispatchIntent,
    WorkflowControlDispatchIntentError,
    command_intent_payload,
    start_intent_payload,
)
from agent.services.workflow_control_dispatch_persistence_rules import (
    _claimable,
    _lease_seconds,
    _reason,
    _start_intent_id,
)
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.security import (
    InMemoryReplayNonceStore,
    ReplayNonceStore,
)


class InMemoryWorkflowControlDispatchIntentStore:
    """Process-local development adapter; production must use the SQL store.

    This adapter deliberately does not claim cross-object transactionality.  It
    is useful for deterministic unit tests and local-only composition; restart
    and multi-process guarantees belong to the SQL implementation below.
    """

    def __init__(
        self,
        bindings: InMemoryWorkflowControlBindingStore,
        *,
        clock: Any = time.time,
        replay_store: ReplayNonceStore | None = None,
    ) -> None:
        self._bindings = bindings
        self._clock = clock
        self._replay_store = replay_store or InMemoryReplayNonceStore()
        self._rows: dict[str, WorkflowControlDispatchIntent] = {}
        self._active: dict[str, str] = {}
        self._lock = threading.RLock()

    def stage_command(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command: SignedWorkflowCommand,
    ) -> WorkflowControlDispatchIntent:
        payload = command_intent_payload(command)
        with self._lock:
            if self._bindings.active_transition_id(binding.workflow_id):
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_cas_conflict")
            existing = self._rows.get(command.command_id)
            if existing is not None:
                if (
                    existing.kind != DISPATCH_KIND_COMMAND
                    or existing.tenant_id != binding.tenant_id
                    or existing.workflow_id != binding.workflow_id
                    or existing.run_id != binding.run_id
                    or existing.payload != payload
                ):
                    raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_conflict")
                return deepcopy(existing)
            if binding.workflow_id in self._active:
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_active_conflict")
            if not self._replay_store.consume(
                tenant_id=command.tenant_id,
                nonce=command.nonce,
                expires_at=command.expires_at,
            ):
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_command_replay_detected")
            self._bindings.claim_command(
                binding.workflow_id,
                expected_revision=command.expected_revision,
                checkpoint_id=command.checkpoint_id,
                command_id=command.command_id,
            )
            try:
                self._bindings.bind_dispatch_intent(
                    binding.workflow_id,
                    intent_id=command.command_id,
                )
                row = WorkflowControlDispatchIntent(
                    intent_id=command.command_id,
                    kind=DISPATCH_KIND_COMMAND,
                    tenant_id=binding.tenant_id,
                    workflow_id=binding.workflow_id,
                    run_id=binding.run_id,
                    payload=payload,
                    available_at=float(self._clock()),
                )
                self._rows[row.intent_id] = row
                self._active[row.workflow_id] = row.intent_id
                return deepcopy(row)
            except Exception:
                self._bindings.release_command(
                    binding.workflow_id,
                    command_id=command.command_id,
                )
                raise

    def stage_start(
        self,
        *,
        binding: WorkflowControlRunBinding,
        start_command: dict[str, Any],
        request_id: str,
        pending_status: dict[str, Any],
    ) -> WorkflowControlDispatchIntent:
        payload = start_intent_payload(start_command, request_id=request_id)
        intent_id = _start_intent_id(binding.workflow_id)
        with self._lock:
            if self._bindings.active_transition_id(binding.workflow_id):
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_cas_conflict")
            existing = self._rows.get(intent_id)
            if existing is not None:
                if (
                    existing.kind != DISPATCH_KIND_START
                    or existing.tenant_id != binding.tenant_id
                    or existing.workflow_id != binding.workflow_id
                    or existing.run_id != binding.run_id
                    or existing.payload != payload
                ):
                    raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_conflict")
                return deepcopy(existing)
            if binding.workflow_id in self._active:
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_active_conflict")
            row = WorkflowControlDispatchIntent(
                intent_id=intent_id,
                kind=DISPATCH_KIND_START,
                tenant_id=binding.tenant_id,
                workflow_id=binding.workflow_id,
                run_id=binding.run_id,
                payload=payload,
                available_at=float(self._clock()),
            )
            self._bindings.record_status(binding.workflow_id, pending_status)
            self._bindings.record_public_status(binding.workflow_id, pending_status)
            self._bindings.bind_dispatch_intent(
                binding.workflow_id,
                intent_id=intent_id,
            )
            self._rows[row.intent_id] = row
            self._active[row.workflow_id] = row.intent_id
            return deepcopy(row)

    def get_active(self, workflow_id: str) -> WorkflowControlDispatchIntent | None:
        with self._lock:
            intent_id = self._active.get(str(workflow_id or "").strip())
            row = self._rows.get(intent_id) if intent_id else None
            return deepcopy(row) if row is not None else None

    def get(self, intent_id: str) -> WorkflowControlDispatchIntent | None:
        with self._lock:
            row = self._rows.get(str(intent_id or "").strip())
            return deepcopy(row) if row is not None else None

    def claim(
        self,
        intent_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> WorkflowControlDispatchIntent | None:
        now = float(self._clock())
        with self._lock:
            row = self._rows.get(str(intent_id))
            if (
                row is None
                or self._bindings.active_transition_id(row.workflow_id)
                or not _claimable(row, now=now, owner_id=owner_id)
            ):
                return None
            phase = row.phase
            claimed = replace(
                row,
                state=DISPATCH_STATE_DISPATCHING,
                dispatch_from_state=phase,
                attempt_count=row.attempt_count + 1,
                lease_owner=str(owner_id),
                lease_expires_at=now + _lease_seconds(lease_seconds),
                revision=row.revision + 1,
            )
            self._rows[row.intent_id] = claimed
            return deepcopy(claimed)

    def claim_due(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        limit: int,
    ) -> tuple[WorkflowControlDispatchIntent, ...]:
        bounded = max(1, min(int(limit), 1000))
        with self._lock:
            ids = [
                row.intent_id
                for row in sorted(
                    self._rows.values(),
                    key=lambda value: (value.available_at, value.intent_id),
                )
                if row.available_at <= float(self._clock())
            ]
        claimed = []
        for intent_id in ids:
            row = self.claim(
                intent_id,
                owner_id=owner_id,
                lease_seconds=lease_seconds,
            )
            if row is not None:
                claimed.append(row)
            if len(claimed) >= bounded:
                break
        return tuple(claimed)

    def acknowledge(
        self,
        intent_id: str,
        *,
        owner_id: str,
        acknowledgement_revision: int = 0,
        acknowledgement_status: str = "",
    ) -> WorkflowControlDispatchIntent:
        with self._lock:
            row = self._owned_dispatch(intent_id, owner_id)
            acknowledged = replace(
                row,
                dispatch_from_state=DISPATCH_STATE_OBSERVATION_PENDING,
                acknowledgement_revision=int(acknowledgement_revision),
                acknowledgement_status=str(acknowledgement_status),
                revision=row.revision + 1,
            )
            if row.kind == DISPATCH_KIND_COMMAND:
                self._bindings.mark_command_observation_pending(
                    row.workflow_id,
                    command_id=row.intent_id,
                    minimum_revision=int(acknowledgement_revision),
                    expected_status=str(acknowledgement_status),
                    reconciliation_ready=False,
                )
            self._rows[row.intent_id] = acknowledged
            return deepcopy(acknowledged)

    def release(
        self,
        intent_id: str,
        *,
        owner_id: str,
        reason_code: str,
        retry_at: float,
    ) -> None:
        with self._lock:
            row = self._owned_dispatch(intent_id, owner_id)
            self._rows[row.intent_id] = replace(
                row,
                state=row.dispatch_from_state,
                lease_owner="",
                lease_expires_at=0.0,
                available_at=max(0.0, float(retry_at)),
                last_error=_reason(reason_code),
                revision=row.revision + 1,
            )

    def complete(
        self,
        intent_id: str,
        *,
        owner_id: str,
        status: dict[str, Any],
    ) -> None:
        with self._lock:
            row = self._owned_dispatch(intent_id, owner_id)
            self._bindings.record_public_status(row.workflow_id, status)
            if row.kind == DISPATCH_KIND_COMMAND:
                self._bindings.finish_command(
                    row.workflow_id,
                    command_id=row.intent_id,
                    status=status,
                )
            else:
                self._bindings.record_status(row.workflow_id, status)
            self._bindings.clear_dispatch_intent(
                row.workflow_id,
                intent_id=row.intent_id,
            )
            self._rows[row.intent_id] = replace(
                row,
                state=DISPATCH_STATE_COMPLETED,
                lease_owner="",
                lease_expires_at=0.0,
                last_error="",
                revision=row.revision + 1,
            )
            self._active.pop(row.workflow_id, None)

    def reject(
        self,
        intent_id: str,
        *,
        owner_id: str,
        reason_code: str,
        status: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            row = self._owned_dispatch(intent_id, owner_id)
            if row.kind == DISPATCH_KIND_COMMAND:
                if status is None:
                    self._bindings.release_command(
                        row.workflow_id,
                        command_id=row.intent_id,
                    )
                else:
                    self._bindings.record_public_status(row.workflow_id, status)
                    self._bindings.finish_command(
                        row.workflow_id,
                        command_id=row.intent_id,
                        status=status,
                    )
            self._bindings.clear_dispatch_intent(
                row.workflow_id,
                intent_id=row.intent_id,
            )
            self._rows[row.intent_id] = replace(
                row,
                state=DISPATCH_STATE_REJECTED,
                lease_owner="",
                lease_expires_at=0.0,
                last_error=_reason(reason_code),
                revision=row.revision + 1,
            )
            self._active.pop(row.workflow_id, None)

    def _owned_dispatch(
        self,
        intent_id: str,
        owner_id: str,
    ) -> WorkflowControlDispatchIntent:
        row = self._rows.get(str(intent_id))
        if (
            row is None
            or self._bindings.active_transition_id(row.workflow_id)
            or row.state != DISPATCH_STATE_DISPATCHING
            or row.lease_owner != str(owner_id)
        ):
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_lease_conflict")
        return row
