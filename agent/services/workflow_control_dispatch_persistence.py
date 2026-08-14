"""Persistence adapters for the Hub workflow-control dispatch outbox."""

from __future__ import annotations

import hashlib
import threading
import time
from copy import deepcopy
from dataclasses import replace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.workflow_runtime import (
    WorkflowCommandNonceDB,
    WorkflowControlBindingDB,
    WorkflowControlDispatchIntentDB,
)
from agent.services.workflow_control_bindings import (
    InMemoryWorkflowControlBindingStore,
    WorkflowControlRunBinding,
    assert_public_status_progression,
)
from agent.services.workflow_control_dispatch_intents import (
    DISPATCH_KIND_COMMAND,
    DISPATCH_KIND_START,
    DISPATCH_STATE_COMPLETED,
    DISPATCH_STATE_DISPATCHING,
    DISPATCH_STATE_OBSERVATION_PENDING,
    DISPATCH_STATE_READY,
    DISPATCH_STATE_REJECTED,
    WorkflowControlDispatchIntent,
    WorkflowControlDispatchIntentError,
    command_intent_payload,
    start_intent_payload,
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
            if row is None or not _claimable(row, now=now, owner_id=owner_id):
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
        if row is None or row.state != DISPATCH_STATE_DISPATCHING or row.lease_owner != str(owner_id):
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_lease_conflict")
        return row


class SQLAlchemyWorkflowControlDispatchIntentStore:
    """Transactional production outbox coupled to the Hub binding row."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Any = time.time,
        fault_injector: Any | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._fault_injector = fault_injector or (lambda _stage: None)

    def stage_command(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command: SignedWorkflowCommand,
    ) -> WorkflowControlDispatchIntent:
        payload = command_intent_payload(command)
        now = float(self._clock())
        with Session(self._engine) as session:
            existing = session.get(WorkflowControlDispatchIntentDB, command.command_id)
            if existing is not None:
                parsed = _intent(existing)
                if (
                    parsed.kind != DISPATCH_KIND_COMMAND
                    or parsed.tenant_id != binding.tenant_id
                    or parsed.workflow_id != binding.workflow_id
                    or parsed.run_id != binding.run_id
                    or parsed.payload != payload
                ):
                    raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_conflict")
                return parsed
            row = session.get(WorkflowControlBindingDB, binding.workflow_id)
            _assert_binding_row(row, binding)
            if (
                row is None
                or int(row.runtime_revision) != command.expected_revision
                or str(row.runtime_checkpoint_ref) != command.checkpoint_id
                or bool(row.command_observation_pending)
                or str(row.dispatch_intent_id or "")
                or str(row.command_receipt_id or "")
                or (row.scheduler_owner and float(row.scheduler_lease_expires_at) > now)
                or (row.command_claim and float(row.command_claim_expires_at) > now)
            ):
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_cas_conflict")
            intent = WorkflowControlDispatchIntentDB(
                id=command.command_id,
                kind=DISPATCH_KIND_COMMAND,
                tenant_id=binding.tenant_id,
                workflow_id=binding.workflow_id,
                run_id=binding.run_id,
                payload=deepcopy(payload),
                state=DISPATCH_STATE_READY,
                dispatch_from_state=DISPATCH_STATE_READY,
                acknowledgement_revision=0,
                acknowledgement_status="",
                attempt_count=0,
                available_at=now,
                lease_owner="",
                lease_expires_at=0.0,
                last_error="",
                revision=1,
                created_at=now,
                updated_at=now,
            )
            session.add(intent)
            session.exec(sa.delete(WorkflowCommandNonceDB).where(WorkflowCommandNonceDB.expires_at <= now))
            nonce_hash = hashlib.sha256(command.nonce.encode("utf-8")).hexdigest()
            session.add(
                WorkflowCommandNonceDB(
                    id=hashlib.sha256(f"{command.tenant_id}\0{nonce_hash}".encode("utf-8")).hexdigest(),
                    tenant_id=command.tenant_id,
                    nonce_hash=nonce_hash,
                    expires_at=float(command.expires_at),
                    consumed_at=now,
                )
            )
            self._fault_injector("command_staged_before_binding_cas")
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == row.id,
                    WorkflowControlBindingDB.revision == int(row.revision),
                    WorkflowControlBindingDB.runtime_revision == command.expected_revision,
                    WorkflowControlBindingDB.runtime_checkpoint_ref == command.checkpoint_id,
                    WorkflowControlBindingDB.dispatch_intent_id == "",
                    WorkflowControlBindingDB.command_receipt_id == "",
                    WorkflowControlBindingDB.command_observation_pending.is_(False),
                    sa.or_(
                        WorkflowControlBindingDB.scheduler_owner == "",
                        WorkflowControlBindingDB.scheduler_lease_expires_at <= now,
                    ),
                    sa.or_(
                        WorkflowControlBindingDB.command_claim == "",
                        WorkflowControlBindingDB.command_claim_expires_at <= now,
                    ),
                )
                .values(
                    dispatch_intent_id=command.command_id,
                    command_claim=command.command_id,
                    command_claim_expires_at=now + 300.0,
                    command_observation_pending=False,
                    command_observation_min_revision=0,
                    command_observation_expected_status="",
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_cas_conflict")
            try:
                session.commit()
            except IntegrityError as exc:
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_conflict") from exc
            return _intent(intent)

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
        now = float(self._clock())
        with Session(self._engine) as session:
            existing = session.get(WorkflowControlDispatchIntentDB, intent_id)
            if existing is not None:
                return _assert_exact_start_intent(
                    _intent(existing),
                    binding=binding,
                    payload=payload,
                )
            row = session.get(WorkflowControlBindingDB, binding.workflow_id)
            _assert_binding_row(row, binding)
            if row is not None and str(row.dispatch_intent_id or ""):
                active = session.get(
                    WorkflowControlDispatchIntentDB,
                    str(row.dispatch_intent_id),
                )
                if active is not None:
                    return _assert_exact_start_intent(
                        _intent(active),
                        binding=binding,
                        payload=payload,
                    )
            if (
                row is None
                or str(row.dispatch_intent_id or "")
                or str(row.command_receipt_id or "")
                or bool(row.command_observation_pending)
                or (row.scheduler_owner and float(row.scheduler_lease_expires_at) > now)
                or (row.command_claim and float(row.command_claim_expires_at) > now)
            ):
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_cas_conflict")
            intent = WorkflowControlDispatchIntentDB(
                id=intent_id,
                kind=DISPATCH_KIND_START,
                tenant_id=binding.tenant_id,
                workflow_id=binding.workflow_id,
                run_id=binding.run_id,
                payload=deepcopy(payload),
                state=DISPATCH_STATE_READY,
                dispatch_from_state=DISPATCH_STATE_READY,
                acknowledgement_revision=0,
                acknowledgement_status="",
                attempt_count=0,
                available_at=now,
                lease_owner="",
                lease_expires_at=0.0,
                last_error="",
                revision=1,
                created_at=now,
                updated_at=now,
            )
            session.add(intent)
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == row.id,
                    WorkflowControlBindingDB.revision == int(row.revision),
                    WorkflowControlBindingDB.dispatch_intent_id == "",
                    WorkflowControlBindingDB.command_receipt_id == "",
                    WorkflowControlBindingDB.command_observation_pending.is_(False),
                    sa.or_(
                        WorkflowControlBindingDB.scheduler_owner == "",
                        WorkflowControlBindingDB.scheduler_lease_expires_at <= now,
                    ),
                    sa.or_(
                        WorkflowControlBindingDB.command_claim == "",
                        WorkflowControlBindingDB.command_claim_expires_at <= now,
                    ),
                )
                .values(
                    dispatch_intent_id=intent_id,
                    last_status=deepcopy(pending_status),
                    public_status=deepcopy(pending_status),
                    runtime_revision=_status_revision(pending_status),
                    runtime_checkpoint_ref=_status_checkpoint(
                        pending_status,
                        fallback=str(row.runtime_checkpoint_ref),
                    ),
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                return self._adopt_start_after_race(
                    intent_id=intent_id,
                    binding=binding,
                    payload=payload,
                )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                try:
                    return self._adopt_start_after_race(
                        intent_id=intent_id,
                        binding=binding,
                        payload=payload,
                    )
                except WorkflowControlDispatchIntentError:
                    raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_conflict") from exc
            return _intent(intent)

    def _adopt_start_after_race(
        self,
        *,
        intent_id: str,
        binding: WorkflowControlRunBinding,
        payload: dict[str, Any],
    ) -> WorkflowControlDispatchIntent:
        with Session(self._engine) as session:
            row = session.get(WorkflowControlBindingDB, binding.workflow_id)
            _assert_binding_row(row, binding)
            active_id = str(row.dispatch_intent_id or "") if row is not None else ""
            if active_id != intent_id:
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_cas_conflict")
            existing = session.get(WorkflowControlDispatchIntentDB, intent_id)
            if existing is None:
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_cas_conflict")
            return _assert_exact_start_intent(
                _intent(existing),
                binding=binding,
                payload=payload,
            )

    def get_active(self, workflow_id: str) -> WorkflowControlDispatchIntent | None:
        normalized = str(workflow_id or "").strip()
        with Session(self._engine) as session:
            binding = session.get(WorkflowControlBindingDB, normalized)
            intent_id = str(binding.dispatch_intent_id or "") if binding is not None else ""
            row = session.get(WorkflowControlDispatchIntentDB, intent_id) if intent_id else None
            return _intent(row) if row is not None else None

    def get(self, intent_id: str) -> WorkflowControlDispatchIntent | None:
        with Session(self._engine) as session:
            row = session.get(
                WorkflowControlDispatchIntentDB,
                str(intent_id or "").strip(),
            )
            return _intent(row) if row is not None else None

    def claim(
        self,
        intent_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> WorkflowControlDispatchIntent | None:
        now = float(self._clock())
        with Session(self._engine) as session:
            row = session.get(WorkflowControlDispatchIntentDB, str(intent_id))
            if row is None or not _claimable(_intent(row), now=now, owner_id=owner_id):
                return None
            phase = _intent(row).phase
            result = session.exec(
                sa.update(WorkflowControlDispatchIntentDB)
                .where(
                    WorkflowControlDispatchIntentDB.id == row.id,
                    WorkflowControlDispatchIntentDB.revision == int(row.revision),
                    _claimable_sql(now=now, owner_id=owner_id),
                )
                .values(
                    state=DISPATCH_STATE_DISPATCHING,
                    dispatch_from_state=phase,
                    attempt_count=int(row.attempt_count) + 1,
                    lease_owner=str(owner_id),
                    lease_expires_at=now + _lease_seconds(lease_seconds),
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                return None
            session.commit()
            refreshed = session.get(WorkflowControlDispatchIntentDB, row.id)
            return _intent(refreshed) if refreshed is not None else None

    def claim_due(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        limit: int,
    ) -> tuple[WorkflowControlDispatchIntent, ...]:
        bounded = max(1, min(int(limit), 1000))
        now = float(self._clock())
        with Session(self._engine) as session:
            ids = session.exec(
                select(WorkflowControlDispatchIntentDB.id)
                .where(
                    WorkflowControlDispatchIntentDB.available_at <= now,
                    _claimable_sql(now=now, owner_id=owner_id),
                )
                .order_by(
                    WorkflowControlDispatchIntentDB.available_at.asc(),
                    WorkflowControlDispatchIntentDB.created_at.asc(),
                )
                .limit(bounded * 4)
            ).all()
        claimed = []
        for intent_id in ids:
            row = self.claim(
                str(intent_id),
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
        acknowledgement_revision = _ack_revision(acknowledgement_revision)
        acknowledgement_status = _ack_status(acknowledgement_status)
        now = float(self._clock())
        with Session(self._engine) as session:
            row = session.get(WorkflowControlDispatchIntentDB, str(intent_id))
            if not _owned(row, owner_id):
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_lease_conflict")
            result = session.exec(
                sa.update(WorkflowControlDispatchIntentDB)
                .where(
                    WorkflowControlDispatchIntentDB.id == row.id,
                    WorkflowControlDispatchIntentDB.revision == int(row.revision),
                    WorkflowControlDispatchIntentDB.state == DISPATCH_STATE_DISPATCHING,
                    WorkflowControlDispatchIntentDB.lease_owner == str(owner_id),
                )
                .values(
                    dispatch_from_state=DISPATCH_STATE_OBSERVATION_PENDING,
                    acknowledgement_revision=acknowledgement_revision,
                    acknowledgement_status=acknowledgement_status,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_lease_conflict")
            if row.kind == DISPATCH_KIND_COMMAND:
                binding_result = session.exec(
                    sa.update(WorkflowControlBindingDB)
                    .where(
                        WorkflowControlBindingDB.id == row.workflow_id,
                        WorkflowControlBindingDB.dispatch_intent_id == row.id,
                        WorkflowControlBindingDB.command_claim == row.id,
                    )
                    .values(
                        command_observation_pending=True,
                        command_observation_min_revision=acknowledgement_revision,
                        command_observation_expected_status=acknowledgement_status,
                        revision=WorkflowControlBindingDB.revision + 1,
                        updated_at=now,
                    )
                )
                if int(binding_result.rowcount or 0) != 1:
                    session.rollback()
                    raise WorkflowControlDispatchIntentError("workflow_control_dispatch_acknowledgement_conflict")
            session.commit()
            refreshed = session.get(WorkflowControlDispatchIntentDB, row.id)
            if refreshed is None:
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_intent_missing")
            return _intent(refreshed)

    def release(
        self,
        intent_id: str,
        *,
        owner_id: str,
        reason_code: str,
        retry_at: float,
    ) -> None:
        now = float(self._clock())
        with Session(self._engine) as session:
            row = session.get(WorkflowControlDispatchIntentDB, str(intent_id))
            if not _owned(row, owner_id):
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_lease_conflict")
            result = session.exec(
                sa.update(WorkflowControlDispatchIntentDB)
                .where(
                    WorkflowControlDispatchIntentDB.id == row.id,
                    WorkflowControlDispatchIntentDB.revision == int(row.revision),
                    WorkflowControlDispatchIntentDB.state == DISPATCH_STATE_DISPATCHING,
                    WorkflowControlDispatchIntentDB.lease_owner == str(owner_id),
                )
                .values(
                    state=str(row.dispatch_from_state),
                    lease_owner="",
                    lease_expires_at=0.0,
                    available_at=max(now, float(retry_at)),
                    last_error=_reason(reason_code),
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_lease_conflict")
            session.commit()

    def complete(
        self,
        intent_id: str,
        *,
        owner_id: str,
        status: dict[str, Any],
    ) -> None:
        safe_status = deepcopy(status)
        now = float(self._clock())
        with Session(self._engine) as session:
            row = session.get(WorkflowControlDispatchIntentDB, str(intent_id))
            if not _owned(row, owner_id):
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_lease_conflict")
            binding = session.get(WorkflowControlBindingDB, str(row.workflow_id))
            if binding is None or str(binding.dispatch_intent_id or "") != row.id:
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_completion_conflict")
            if row.kind == DISPATCH_KIND_COMMAND:
                if binding.command_claim != row.id:
                    raise WorkflowControlDispatchIntentError("workflow_control_dispatch_completion_conflict")
                _assert_ack_fence(row, safe_status)
            try:
                assert_public_status_progression(
                    dict(binding.public_status or {}) or None,
                    safe_status,
                )
            except RuntimeError as exc:
                raise WorkflowControlDispatchIntentError(str(exc)) from exc
            binding_result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == binding.id,
                    WorkflowControlBindingDB.revision == int(binding.revision),
                    WorkflowControlBindingDB.dispatch_intent_id == row.id,
                    WorkflowControlBindingDB.command_receipt_id == "",
                    *((WorkflowControlBindingDB.command_claim == row.id,) if row.kind == DISPATCH_KIND_COMMAND else ()),
                )
                .values(
                    last_status=safe_status,
                    public_status=safe_status,
                    runtime_revision=_status_revision(safe_status),
                    runtime_checkpoint_ref=_status_checkpoint(
                        safe_status,
                        fallback=str(binding.runtime_checkpoint_ref),
                    ),
                    dispatch_intent_id="",
                    command_claim="",
                    command_claim_expires_at=0.0,
                    command_observation_pending=False,
                    command_observation_min_revision=0,
                    command_observation_expected_status="",
                    revision=int(binding.revision) + 1,
                    updated_at=now,
                )
            )
            intent_result = session.exec(
                sa.update(WorkflowControlDispatchIntentDB)
                .where(
                    WorkflowControlDispatchIntentDB.id == row.id,
                    WorkflowControlDispatchIntentDB.revision == int(row.revision),
                    WorkflowControlDispatchIntentDB.state == DISPATCH_STATE_DISPATCHING,
                    WorkflowControlDispatchIntentDB.lease_owner == str(owner_id),
                )
                .values(
                    state=DISPATCH_STATE_COMPLETED,
                    lease_owner="",
                    lease_expires_at=0.0,
                    last_error="",
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(binding_result.rowcount or 0) != 1 or int(intent_result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_completion_conflict")
            session.commit()

    def reject(
        self,
        intent_id: str,
        *,
        owner_id: str,
        reason_code: str,
        status: dict[str, Any] | None = None,
    ) -> None:
        safe_status = deepcopy(status) if status is not None else None
        now = float(self._clock())
        with Session(self._engine) as session:
            row = session.get(WorkflowControlDispatchIntentDB, str(intent_id))
            if not _owned(row, owner_id) or row is None or row.kind != DISPATCH_KIND_COMMAND:
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_lease_conflict")
            binding = session.get(WorkflowControlBindingDB, str(row.workflow_id))
            if (
                binding is None
                or str(binding.dispatch_intent_id or "") != row.id
                or str(binding.command_claim or "") != row.id
                or bool(binding.command_observation_pending)
            ):
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_completion_conflict")
            status_values: dict[str, Any] = {}
            if safe_status is not None:
                try:
                    assert_public_status_progression(
                        dict(binding.public_status or {}) or None,
                        safe_status,
                    )
                except RuntimeError as exc:
                    raise WorkflowControlDispatchIntentError(str(exc)) from exc
                status_values = {
                    "last_status": safe_status,
                    "public_status": safe_status,
                    "runtime_revision": _status_revision(safe_status),
                    "runtime_checkpoint_ref": _status_checkpoint(
                        safe_status,
                        fallback=str(binding.runtime_checkpoint_ref),
                    ),
                }
            binding_result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == binding.id,
                    WorkflowControlBindingDB.revision == int(binding.revision),
                    WorkflowControlBindingDB.dispatch_intent_id == row.id,
                    WorkflowControlBindingDB.command_claim == row.id,
                    WorkflowControlBindingDB.command_observation_pending.is_(False),
                )
                .values(
                    **status_values,
                    dispatch_intent_id="",
                    command_claim="",
                    command_claim_expires_at=0.0,
                    command_observation_min_revision=0,
                    command_observation_expected_status="",
                    revision=int(binding.revision) + 1,
                    updated_at=now,
                )
            )
            intent_result = session.exec(
                sa.update(WorkflowControlDispatchIntentDB)
                .where(
                    WorkflowControlDispatchIntentDB.id == row.id,
                    WorkflowControlDispatchIntentDB.revision == int(row.revision),
                    WorkflowControlDispatchIntentDB.state == DISPATCH_STATE_DISPATCHING,
                    WorkflowControlDispatchIntentDB.lease_owner == str(owner_id),
                )
                .values(
                    state=DISPATCH_STATE_REJECTED,
                    lease_owner="",
                    lease_expires_at=0.0,
                    last_error=_reason(reason_code),
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(binding_result.rowcount or 0) != 1 or int(intent_result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_completion_conflict")
            session.commit()


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


__all__ = [
    "InMemoryWorkflowControlDispatchIntentStore",
    "SQLAlchemyWorkflowControlDispatchIntentStore",
]
