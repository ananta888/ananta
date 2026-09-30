"""Persistence adapters for the Hub workflow-control dispatch outbox.

The SQL adapter lives here; the in-memory adapter and the shared row-mapping
and fencing rules live in ``workflow_control_dispatch_persistence_{memory,rules}``.
"""

from __future__ import annotations

import hashlib
import time
from copy import deepcopy
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
from agent.services.workflow_control_dispatch_persistence_memory import (  # noqa: F401 - public re-export
    InMemoryWorkflowControlDispatchIntentStore,
)
from agent.services.workflow_control_dispatch_persistence_rules import (
    _ack_revision,
    _ack_status,
    _assert_ack_fence,
    _assert_binding_row,
    _assert_exact_start_intent,
    _claimable,
    _claimable_sql,
    _intent,
    _lease_seconds,
    _owned,
    _reason,
    _start_intent_id,
    _status_checkpoint,
    _status_revision,
)
from agent.services.workflow_runtime.commands import SignedWorkflowCommand


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
                active_binding = session.get(WorkflowControlBindingDB, binding.workflow_id)
                if active_binding is not None and active_binding.active_transition_id:
                    raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_cas_conflict")
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
                or str(row.active_transition_id or "")
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
                    WorkflowControlBindingDB.active_transition_id == "",
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
                active_binding = session.get(WorkflowControlBindingDB, binding.workflow_id)
                if active_binding is not None and active_binding.active_transition_id:
                    raise WorkflowControlDispatchIntentError("workflow_control_dispatch_stage_cas_conflict")
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
                or str(row.active_transition_id or "")
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
                    WorkflowControlBindingDB.active_transition_id == "",
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
            if active_id != intent_id or (row is not None and row.active_transition_id):
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
            binding = session.get(WorkflowControlBindingDB, str(row.workflow_id)) if row is not None else None
            if (
                row is None
                or binding is None
                or binding.active_transition_id
                or not _claimable(_intent(row), now=now, owner_id=owner_id)
            ):
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
            binding = session.get(WorkflowControlBindingDB, str(row.workflow_id)) if row is not None else None
            if not _owned(row, owner_id) or binding is None or binding.active_transition_id:
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
                        WorkflowControlBindingDB.active_transition_id == "",
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
            binding = session.get(WorkflowControlBindingDB, str(row.workflow_id)) if row is not None else None
            if not _owned(row, owner_id) or binding is None or binding.active_transition_id:
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
            if (
                binding is None
                or binding.active_transition_id
                or str(binding.dispatch_intent_id or "") != row.id
            ):
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
                    WorkflowControlBindingDB.active_transition_id == "",
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
                or bool(binding.active_transition_id)
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
                    WorkflowControlBindingDB.active_transition_id == "",
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


__all__ = [
    "InMemoryWorkflowControlDispatchIntentStore",
    "SQLAlchemyWorkflowControlDispatchIntentStore",
]
