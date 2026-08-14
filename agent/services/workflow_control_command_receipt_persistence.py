"""Persistence adapters for Hub synchronous command idempotency receipts."""

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
    WorkflowControlCommandReceiptDB,
)
from agent.services.workflow_control_bindings import (
    InMemoryWorkflowControlBindingStore,
    WorkflowControlRunBinding,
)
from agent.services.workflow_control_command_receipts import (
    COMMAND_RECEIPT_COMPLETED,
    COMMAND_RECEIPT_DISPATCHING,
    COMMAND_RECEIPT_PENDING,
    COMMAND_RECEIPT_REJECTED,
    WorkflowControlCommandReceipt,
    WorkflowControlCommandReceiptError,
    WorkflowControlCommandRejectedError,
    admitted_receipt_command,
    assert_exact_receipt_request,
    validate_persisted_public_status,
    validate_result_status,
)
from agent.services.workflow_runtime.security import (
    InMemoryReplayNonceStore,
    ReplayNonceStore,
)
from agent.services.workflow_transition_outbox import workflow_transition_request_fingerprint


class InMemoryWorkflowControlCommandReceiptStore:
    """Process-local adapter for tests and explicit local-only composition."""

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
        self._rows: dict[str, WorkflowControlCommandReceipt] = {}
        self._lock = threading.RLock()

    def stage(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command_id: str,
        actor_id: str,
        command_type: str,
        request_payload: dict[str, Any],
        expected_revision: int,
        checkpoint_ref: str,
    ) -> WorkflowControlCommandReceipt:
        candidate = WorkflowControlCommandReceipt(
            command_id=command_id,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            actor_id=actor_id,
            command_type=command_type,
            request_payload=deepcopy(request_payload),
            expected_revision=expected_revision,
            checkpoint_ref=checkpoint_ref,
            request_fingerprint=workflow_transition_request_fingerprint(request_payload),
        )
        command = admitted_receipt_command(candidate)
        with self._lock:
            existing = self._rows.get(candidate.command_id)
            if existing is not None:
                assert_exact_receipt_request(
                    existing,
                    binding=binding,
                    actor_id=actor_id,
                    command_type=command_type,
                    request_payload=request_payload,
                )
                if not existing.transition_id and self._bindings.active_transition_id(binding.workflow_id):
                    raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")
                return deepcopy(existing)
            if self._bindings.active_transition_id(binding.workflow_id):
                raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")
            self._bindings.bind_command_receipt(
                binding.workflow_id,
                receipt_id=candidate.command_id,
                expected_revision=candidate.expected_revision,
                checkpoint_ref=candidate.checkpoint_ref,
            )
            self._rows[candidate.command_id] = candidate
            try:
                consumed = self._replay_store.consume(
                    tenant_id=command.tenant_id,
                    nonce=command.nonce,
                    expires_at=command.expires_at,
                )
            except Exception:
                self._rows.pop(candidate.command_id, None)
                self._bindings.reject_command_receipt(
                    binding.workflow_id,
                    receipt_id=candidate.command_id,
                )
                raise
            if not consumed:
                self._rows.pop(candidate.command_id, None)
                self._bindings.reject_command_receipt(
                    binding.workflow_id,
                    receipt_id=candidate.command_id,
                )
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_replay_detected")
            return deepcopy(candidate)

    def get(self, command_id: str) -> WorkflowControlCommandReceipt | None:
        with self._lock:
            row = self._rows.get(str(command_id or "").strip())
            return deepcopy(row) if row is not None else None

    def claim(
        self,
        command_id: str,
        *,
        owner_id: str,
        lease_seconds: float = 30.0,
    ) -> WorkflowControlCommandReceipt | None:
        now = float(self._clock())
        expires_at = now + _lease_seconds(lease_seconds)
        with self._lock:
            row = self._rows.get(str(command_id or "").strip())
            if row is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            _assert_unattributed_receipt(row)
            if self._bindings.active_transition_id(row.workflow_id):
                raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")
            if row.state != COMMAND_RECEIPT_PENDING and not (
                row.state == COMMAND_RECEIPT_DISPATCHING and row.dispatch_lease_expires_at <= now
            ):
                return None
            claimed = replace(
                row,
                state=COMMAND_RECEIPT_DISPATCHING,
                dispatch_owner=str(owner_id),
                dispatch_lease_expires_at=expires_at,
                dispatch_generation=row.dispatch_generation + 1,
                last_heartbeat_at=now,
                revision=row.revision + 1,
            )
            claimed.__post_init__()
            self._rows[row.command_id] = claimed
            return deepcopy(claimed)

    def release(
        self,
        command_id: str,
        *,
        owner_id: str,
        dispatch_generation: int,
    ) -> WorkflowControlCommandReceipt:
        now = float(self._clock())
        with self._lock:
            row = self._rows.get(str(command_id or "").strip())
            if row is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            _assert_unattributed_receipt(row)
            if self._bindings.active_transition_id(row.workflow_id):
                raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")
            _assert_receipt_owner(row, owner_id, dispatch_generation, now=now)
            released = replace(
                row,
                state=COMMAND_RECEIPT_PENDING,
                dispatch_owner="",
                dispatch_lease_expires_at=0.0,
                revision=row.revision + 1,
            )
            self._rows[row.command_id] = released
            return deepcopy(released)

    def heartbeat(
        self,
        command_id: str,
        *,
        owner_id: str,
        dispatch_generation: int,
        lease_seconds: float = 30.0,
    ) -> WorkflowControlCommandReceipt:
        now = float(self._clock())
        with self._lock:
            row = self._rows.get(str(command_id or "").strip())
            if row is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            _assert_unattributed_receipt(row)
            if self._bindings.active_transition_id(row.workflow_id):
                raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")
            _assert_receipt_owner(row, owner_id, dispatch_generation, now=now)
            next_generation = max(1, row.dispatch_generation)
            updated = replace(
                row,
                dispatch_lease_expires_at=now + _lease_seconds(lease_seconds),
                dispatch_generation=next_generation,
                last_heartbeat_at=now,
                revision=row.revision + 1,
            )
            self._rows[row.command_id] = updated
            return deepcopy(updated)

    def complete(
        self,
        command_id: str,
        *,
        status: dict[str, Any],
        owner_id: str,
        dispatch_generation: int,
    ) -> WorkflowControlCommandReceipt:
        now = float(self._clock())
        with self._lock:
            row = self._rows.get(str(command_id or "").strip())
            if row is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            _assert_unattributed_receipt(row)
            if self._bindings.active_transition_id(row.workflow_id):
                raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")
            _assert_receipt_owner(row, owner_id, dispatch_generation, now=now)
            validate_result_status(row, status)
            binding = self._bindings.get(row.workflow_id)
            if binding is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_binding_missing")
            validate_persisted_public_status(row, binding, status)
            self._bindings.finish_command_receipt(
                row.workflow_id,
                receipt_id=row.command_id,
                status=status,
            )
            completed = replace(
                row,
                state=COMMAND_RECEIPT_COMPLETED,
                result_status=deepcopy(status),
                dispatch_owner="",
                dispatch_lease_expires_at=0.0,
                last_heartbeat_at=now,
                revision=row.revision + 1,
            )
            self._rows[row.command_id] = completed
            return deepcopy(completed)

    def reject(
        self,
        command_id: str,
        *,
        reason_code: str,
        owner_id: str,
        dispatch_generation: int,
    ) -> WorkflowControlCommandReceipt:
        now = float(self._clock())
        with self._lock:
            row = self._rows.get(str(command_id or "").strip())
            if row is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            _assert_unattributed_receipt(row)
            if self._bindings.active_transition_id(row.workflow_id):
                raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")
            _assert_receipt_owner(row, owner_id, dispatch_generation, now=now)
            rejected = replace(
                row,
                state=COMMAND_RECEIPT_REJECTED,
                rejection_reason=str(reason_code),
                dispatch_owner="",
                dispatch_lease_expires_at=0.0,
                last_heartbeat_at=now,
                revision=row.revision + 1,
            )
            # Construct the terminal DTO before freeing the binding marker.
            rejected.__post_init__()
            self._bindings.reject_command_receipt(
                row.workflow_id,
                receipt_id=row.command_id,
            )
            self._rows[row.command_id] = rejected
            return deepcopy(rejected)

    def list_pending(
        self,
        *,
        limit: int = 100,
    ) -> tuple[WorkflowControlCommandReceipt, ...]:
        bounded = max(1, min(int(limit), 1000))
        with self._lock:
            return tuple(
                deepcopy(row)
                for row in sorted(self._rows.values(), key=lambda item: item.command_id)
                if row.state == COMMAND_RECEIPT_PENDING
                or (row.state == COMMAND_RECEIPT_DISPATCHING and row.dispatch_lease_expires_at <= float(self._clock()))
                if not row.transition_id
                if not self._bindings.active_transition_id(row.workflow_id)
            )[:bounded]


class SQLAlchemyWorkflowControlCommandReceiptStore:
    """Transactional production receipt store coupled to its binding marker."""

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

    def stage(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command_id: str,
        actor_id: str,
        command_type: str,
        request_payload: dict[str, Any],
        expected_revision: int,
        checkpoint_ref: str,
    ) -> WorkflowControlCommandReceipt:
        candidate = WorkflowControlCommandReceipt(
            command_id=command_id,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            actor_id=actor_id,
            command_type=command_type,
            request_payload=deepcopy(request_payload),
            expected_revision=expected_revision,
            checkpoint_ref=checkpoint_ref,
            request_fingerprint=workflow_transition_request_fingerprint(request_payload),
        )
        command = admitted_receipt_command(candidate)
        now = float(self._clock())
        if command.expires_at <= now:
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_admission_expired")
        with Session(self._engine) as session:
            existing = session.get(WorkflowControlCommandReceiptDB, candidate.command_id)
            if existing is not None:
                parsed = _receipt(existing)
                assert_exact_receipt_request(
                    parsed,
                    binding=binding,
                    actor_id=actor_id,
                    command_type=command_type,
                    request_payload=request_payload,
                )
                persisted_binding = session.get(WorkflowControlBindingDB, candidate.workflow_id)
                if (
                    not parsed.transition_id
                    and persisted_binding is not None
                    and persisted_binding.active_transition_id
                ):
                    raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")
                return parsed
            row = session.get(WorkflowControlBindingDB, binding.workflow_id)
            _assert_binding(row, binding)
            if (
                row is None
                or int(row.runtime_revision) != candidate.expected_revision
                or str(row.runtime_checkpoint_ref) != candidate.checkpoint_ref
                or str(row.dispatch_intent_id or "")
                or bool(row.command_observation_pending)
                or str(row.command_receipt_id or "")
                or str(row.active_transition_id or "")
                or (row.scheduler_owner and float(row.scheduler_lease_expires_at) > now)
                or (row.command_claim and float(row.command_claim_expires_at) > now)
            ):
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_stage_conflict")
            receipt_row = WorkflowControlCommandReceiptDB(
                id=candidate.command_id,
                tenant_id=candidate.tenant_id,
                workflow_id=candidate.workflow_id,
                run_id=candidate.run_id,
                actor_id=candidate.actor_id,
                command_type=candidate.command_type,
                request_payload=deepcopy(candidate.request_payload),
                expected_revision=candidate.expected_revision,
                checkpoint_ref=candidate.checkpoint_ref,
                state=COMMAND_RECEIPT_PENDING,
                result_status={},
                rejection_reason="",
                dispatch_owner="",
                dispatch_lease_expires_at=0.0,
                request_fingerprint=candidate.request_fingerprint,
                transition_id="",
                effect_fingerprint="",
                outcome_fingerprint="",
                dispatch_generation=0,
                last_heartbeat_at=0.0,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            session.add(receipt_row)
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
            self._fault_injector("receipt_staged_before_binding_cas")
            with session.no_autoflush:
                result = session.exec(
                    sa.update(WorkflowControlBindingDB)
                    .where(
                        WorkflowControlBindingDB.id == row.id,
                        WorkflowControlBindingDB.revision == int(row.revision),
                        WorkflowControlBindingDB.runtime_revision == candidate.expected_revision,
                        WorkflowControlBindingDB.runtime_checkpoint_ref == candidate.checkpoint_ref,
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
                        command_receipt_id=candidate.command_id,
                        revision=int(row.revision) + 1,
                        updated_at=now,
                    )
                )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                return self._adopt_after_race(candidate, binding=binding)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                try:
                    return self._adopt_after_race(candidate, binding=binding)
                except WorkflowControlCommandReceiptError:
                    raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_stage_conflict") from exc
            return _receipt(receipt_row)

    def get(self, command_id: str) -> WorkflowControlCommandReceipt | None:
        with Session(self._engine) as session:
            row = session.get(
                WorkflowControlCommandReceiptDB,
                str(command_id or "").strip(),
            )
            return _receipt(row) if row is not None else None

    def claim(
        self,
        command_id: str,
        *,
        owner_id: str,
        lease_seconds: float = 30.0,
    ) -> WorkflowControlCommandReceipt | None:
        now = float(self._clock())
        expires_at = now + _lease_seconds(lease_seconds)
        normalized_id = str(command_id or "").strip()
        with Session(self._engine) as session:
            row = session.get(WorkflowControlCommandReceiptDB, normalized_id)
            if row is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            receipt = _receipt(row)
            _assert_unattributed_receipt(receipt)
            binding = session.get(WorkflowControlBindingDB, receipt.workflow_id)
            if binding is None or binding.active_transition_id:
                raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")
            result = session.exec(
                sa.update(WorkflowControlCommandReceiptDB)
                .where(
                    WorkflowControlCommandReceiptDB.id == row.id,
                    WorkflowControlCommandReceiptDB.revision == int(row.revision),
                    WorkflowControlCommandReceiptDB.transition_id == "",
                    sa.or_(
                        WorkflowControlCommandReceiptDB.state == COMMAND_RECEIPT_PENDING,
                        sa.and_(
                            WorkflowControlCommandReceiptDB.state == COMMAND_RECEIPT_DISPATCHING,
                            WorkflowControlCommandReceiptDB.dispatch_lease_expires_at <= now,
                        ),
                    ),
                )
                .values(
                    state=COMMAND_RECEIPT_DISPATCHING,
                    dispatch_owner=str(owner_id),
                    dispatch_lease_expires_at=expires_at,
                    dispatch_generation=int(row.dispatch_generation) + 1,
                    last_heartbeat_at=now,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                return None
            session.commit()
            refreshed = session.get(WorkflowControlCommandReceiptDB, normalized_id)
            return _receipt(refreshed) if refreshed is not None else None

    def release(
        self,
        command_id: str,
        *,
        owner_id: str,
        dispatch_generation: int,
    ) -> WorkflowControlCommandReceipt:
        now = float(self._clock())
        normalized_id = str(command_id or "").strip()
        with Session(self._engine) as session:
            row = session.get(WorkflowControlCommandReceiptDB, normalized_id)
            if row is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            receipt = _receipt(row)
            _assert_unattributed_receipt(receipt)
            binding = session.get(WorkflowControlBindingDB, receipt.workflow_id)
            if binding is None or binding.active_transition_id:
                raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")
            _assert_receipt_owner(receipt, owner_id, dispatch_generation, now=now)
            result = session.exec(
                sa.update(WorkflowControlCommandReceiptDB)
                .where(
                    WorkflowControlCommandReceiptDB.id == row.id,
                    WorkflowControlCommandReceiptDB.revision == int(row.revision),
                    WorkflowControlCommandReceiptDB.state == COMMAND_RECEIPT_DISPATCHING,
                    WorkflowControlCommandReceiptDB.dispatch_owner == str(owner_id),
                    WorkflowControlCommandReceiptDB.dispatch_generation == int(dispatch_generation),
                    WorkflowControlCommandReceiptDB.dispatch_lease_expires_at > now,
                    WorkflowControlCommandReceiptDB.transition_id == "",
                )
                .values(
                    state=COMMAND_RECEIPT_PENDING,
                    dispatch_owner="",
                    dispatch_lease_expires_at=0.0,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_lease_conflict")
            session.commit()
            refreshed = session.get(WorkflowControlCommandReceiptDB, normalized_id)
            if refreshed is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            return _receipt(refreshed)

    def heartbeat(
        self,
        command_id: str,
        *,
        owner_id: str,
        dispatch_generation: int,
        lease_seconds: float = 30.0,
    ) -> WorkflowControlCommandReceipt:
        now = float(self._clock())
        normalized_id = str(command_id or "").strip()
        with Session(self._engine) as session:
            row = session.get(WorkflowControlCommandReceiptDB, normalized_id)
            if row is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            receipt = _receipt(row)
            _assert_unattributed_receipt(receipt)
            binding = session.get(WorkflowControlBindingDB, receipt.workflow_id)
            if binding is None or binding.active_transition_id:
                raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")
            _assert_receipt_owner(receipt, owner_id, dispatch_generation, now=now)
            next_generation = max(1, int(row.dispatch_generation))
            result = session.exec(
                sa.update(WorkflowControlCommandReceiptDB)
                .where(
                    WorkflowControlCommandReceiptDB.id == row.id,
                    WorkflowControlCommandReceiptDB.revision == int(row.revision),
                    WorkflowControlCommandReceiptDB.state == COMMAND_RECEIPT_DISPATCHING,
                    WorkflowControlCommandReceiptDB.dispatch_owner == str(owner_id),
                    WorkflowControlCommandReceiptDB.dispatch_generation == int(dispatch_generation),
                    WorkflowControlCommandReceiptDB.dispatch_lease_expires_at > now,
                    WorkflowControlCommandReceiptDB.transition_id == "",
                )
                .values(
                    dispatch_lease_expires_at=now + _lease_seconds(lease_seconds),
                    dispatch_generation=next_generation,
                    last_heartbeat_at=now,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_lease_conflict")
            session.commit()
            refreshed = session.get(WorkflowControlCommandReceiptDB, normalized_id)
            if refreshed is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            return _receipt(refreshed)

    def complete(
        self,
        command_id: str,
        *,
        status: dict[str, Any],
        owner_id: str,
        dispatch_generation: int,
    ) -> WorkflowControlCommandReceipt:
        safe_status = deepcopy(status)
        now = float(self._clock())
        with Session(self._engine) as session:
            row = session.get(
                WorkflowControlCommandReceiptDB,
                str(command_id or "").strip(),
            )
            if row is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            receipt = _receipt(row)
            _assert_unattributed_receipt(receipt)
            _assert_receipt_owner(receipt, owner_id, dispatch_generation, now=now)
            validate_result_status(receipt, safe_status)
            binding = session.get(WorkflowControlBindingDB, receipt.workflow_id)
            if (
                binding is None
                or str(binding.command_receipt_id or "") != receipt.command_id
                or str(binding.command_claim or "")
                or str(binding.active_transition_id or "")
            ):
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_completion_conflict")
            validate_persisted_public_status(receipt, binding, safe_status)
            binding_result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == binding.id,
                    WorkflowControlBindingDB.revision == int(binding.revision),
                    WorkflowControlBindingDB.command_receipt_id == receipt.command_id,
                    WorkflowControlBindingDB.command_claim == "",
                    WorkflowControlBindingDB.active_transition_id == "",
                )
                .values(
                    command_receipt_id="",
                    revision=int(binding.revision) + 1,
                    updated_at=now,
                )
            )
            receipt_result = session.exec(
                sa.update(WorkflowControlCommandReceiptDB)
                .where(
                    WorkflowControlCommandReceiptDB.id == row.id,
                    WorkflowControlCommandReceiptDB.revision == int(row.revision),
                    WorkflowControlCommandReceiptDB.state == COMMAND_RECEIPT_DISPATCHING,
                    WorkflowControlCommandReceiptDB.dispatch_owner == str(owner_id),
                    WorkflowControlCommandReceiptDB.dispatch_generation == int(dispatch_generation),
                    WorkflowControlCommandReceiptDB.dispatch_lease_expires_at > now,
                    WorkflowControlCommandReceiptDB.transition_id == "",
                )
                .values(
                    state=COMMAND_RECEIPT_COMPLETED,
                    result_status=safe_status,
                    dispatch_owner="",
                    dispatch_lease_expires_at=0.0,
                    last_heartbeat_at=now,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(binding_result.rowcount or 0) != 1 or int(receipt_result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_completion_conflict")
            session.commit()
            refreshed = session.get(WorkflowControlCommandReceiptDB, row.id)
            if refreshed is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            return _receipt(refreshed)

    def reject(
        self,
        command_id: str,
        *,
        reason_code: str,
        owner_id: str,
        dispatch_generation: int,
    ) -> WorkflowControlCommandReceipt:
        normalized_reason = WorkflowControlCommandRejectedError(reason_code).reason_code
        now = float(self._clock())
        with Session(self._engine) as session:
            row = session.get(
                WorkflowControlCommandReceiptDB,
                str(command_id or "").strip(),
            )
            if row is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            receipt = _receipt(row)
            _assert_unattributed_receipt(receipt)
            _assert_receipt_owner(receipt, owner_id, dispatch_generation, now=now)
            binding = session.get(WorkflowControlBindingDB, receipt.workflow_id)
            if (
                binding is None
                or str(binding.command_receipt_id or "") != receipt.command_id
                or str(binding.command_claim or "")
                or str(binding.active_transition_id or "")
            ):
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_completion_conflict")
            binding_result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == binding.id,
                    WorkflowControlBindingDB.revision == int(binding.revision),
                    WorkflowControlBindingDB.command_receipt_id == receipt.command_id,
                    WorkflowControlBindingDB.command_claim == "",
                    WorkflowControlBindingDB.active_transition_id == "",
                )
                .values(
                    command_receipt_id="",
                    revision=int(binding.revision) + 1,
                    updated_at=now,
                )
            )
            receipt_result = session.exec(
                sa.update(WorkflowControlCommandReceiptDB)
                .where(
                    WorkflowControlCommandReceiptDB.id == row.id,
                    WorkflowControlCommandReceiptDB.revision == int(row.revision),
                    WorkflowControlCommandReceiptDB.state == COMMAND_RECEIPT_DISPATCHING,
                    WorkflowControlCommandReceiptDB.dispatch_owner == str(owner_id),
                    WorkflowControlCommandReceiptDB.dispatch_generation == int(dispatch_generation),
                    WorkflowControlCommandReceiptDB.dispatch_lease_expires_at > now,
                    WorkflowControlCommandReceiptDB.transition_id == "",
                )
                .values(
                    state=COMMAND_RECEIPT_REJECTED,
                    rejection_reason=normalized_reason,
                    dispatch_owner="",
                    dispatch_lease_expires_at=0.0,
                    last_heartbeat_at=now,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(binding_result.rowcount or 0) != 1 or int(receipt_result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_completion_conflict")
            session.commit()
            refreshed = session.get(WorkflowControlCommandReceiptDB, row.id)
            if refreshed is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_not_found")
            return _receipt(refreshed)

    def list_pending(
        self,
        *,
        limit: int = 100,
    ) -> tuple[WorkflowControlCommandReceipt, ...]:
        bounded = max(1, min(int(limit), 1000))
        with Session(self._engine) as session:
            rows = session.exec(
                select(WorkflowControlCommandReceiptDB)
                .join(
                    WorkflowControlBindingDB,
                    WorkflowControlBindingDB.id == WorkflowControlCommandReceiptDB.workflow_id,
                )
                .where(
                    WorkflowControlCommandReceiptDB.transition_id == "",
                    WorkflowControlBindingDB.active_transition_id == "",
                    sa.or_(
                        WorkflowControlCommandReceiptDB.state == COMMAND_RECEIPT_PENDING,
                        sa.and_(
                            WorkflowControlCommandReceiptDB.state == COMMAND_RECEIPT_DISPATCHING,
                            WorkflowControlCommandReceiptDB.dispatch_lease_expires_at <= float(self._clock()),
                        ),
                    )
                )
                .order_by(WorkflowControlCommandReceiptDB.created_at.asc())
                .limit(bounded)
            ).all()
            return tuple(_receipt(row) for row in rows)

    def _adopt_after_race(
        self,
        candidate: WorkflowControlCommandReceipt,
        *,
        binding: WorkflowControlRunBinding,
    ) -> WorkflowControlCommandReceipt:
        with Session(self._engine) as session:
            row = session.get(WorkflowControlCommandReceiptDB, candidate.command_id)
            persisted_binding = session.get(WorkflowControlBindingDB, binding.workflow_id)
            if (
                row is None
                or persisted_binding is None
                or str(persisted_binding.command_receipt_id or "") != candidate.command_id
            ):
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_stage_conflict")
            receipt = _receipt(row)
            assert_exact_receipt_request(
                receipt,
                binding=binding,
                actor_id=candidate.actor_id,
                command_type=candidate.command_type,
                request_payload=candidate.request_payload,
            )
            return receipt


def _receipt(row: WorkflowControlCommandReceiptDB) -> WorkflowControlCommandReceipt:
    result = deepcopy(dict(row.result_status or {}))
    return WorkflowControlCommandReceipt(
        command_id=str(row.id),
        tenant_id=str(row.tenant_id),
        workflow_id=str(row.workflow_id),
        run_id=str(row.run_id),
        actor_id=str(row.actor_id),
        command_type=str(row.command_type),
        request_payload=deepcopy(dict(row.request_payload)),
        expected_revision=int(row.expected_revision),
        checkpoint_ref=str(row.checkpoint_ref),
        state=str(row.state),
        result_status=result or None,
        rejection_reason=str(row.rejection_reason or ""),
        dispatch_owner=str(row.dispatch_owner or ""),
        dispatch_lease_expires_at=float(row.dispatch_lease_expires_at or 0.0),
        request_fingerprint=str(row.request_fingerprint or ""),
        transition_id=str(row.transition_id or ""),
        effect_fingerprint=str(row.effect_fingerprint or ""),
        outcome_fingerprint=str(row.outcome_fingerprint or ""),
        dispatch_generation=int(row.dispatch_generation or 0),
        last_heartbeat_at=float(row.last_heartbeat_at or 0.0),
        revision=int(row.revision),
    )


def _lease_seconds(value: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_lease_invalid") from exc
    if normalized <= 0 or normalized > 300:
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_lease_invalid")
    return normalized


def _assert_receipt_owner(
    receipt: WorkflowControlCommandReceipt,
    owner_id: str,
    dispatch_generation: int,
    *,
    now: float,
) -> None:
    if (
        receipt.state != COMMAND_RECEIPT_DISPATCHING
        or receipt.dispatch_owner != str(owner_id)
        or receipt.dispatch_generation != int(dispatch_generation)
        or receipt.dispatch_lease_expires_at <= now
    ):
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_lease_conflict")


def _assert_unattributed_receipt(receipt: WorkflowControlCommandReceipt) -> None:
    if receipt.transition_id:
        raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")


def _assert_binding(
    row: WorkflowControlBindingDB | None,
    binding: WorkflowControlRunBinding,
) -> None:
    if row is None or any(
        (
            str(row.tenant_id) != binding.tenant_id,
            str(row.workflow_id) != binding.workflow_id,
            str(row.run_id) != binding.run_id,
            str(row.runtime_id) != binding.runtime_id,
            str(row.plan_hash) != binding.plan_hash,
            str(row.policy_version) != binding.policy_version,
        )
    ):
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_binding_mismatch")


__all__ = [
    "InMemoryWorkflowControlCommandReceiptStore",
    "SQLAlchemyWorkflowControlCommandReceiptStore",
]
