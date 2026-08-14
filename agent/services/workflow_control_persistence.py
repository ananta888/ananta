"""Restart-safe persistence adapter for legacy workflow-control bindings."""

from __future__ import annotations

import hashlib
import time
from copy import deepcopy
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.workflow_runtime import WorkflowCommandNonceDB, WorkflowControlBindingDB
from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_control_bindings import (
    assert_public_status_progression,
    assert_runtime_status_progression,
)


class WorkflowControlBindingPersistenceError(RuntimeError):
    """Stable fail-closed persistence/CAS failure."""


class SQLAlchemyWorkflowControlBindingStore:
    """Persist owner, request and latest runtime projection with optimistic CAS."""

    def __init__(self, engine: Engine, *, clock=time.time) -> None:
        self._engine = engine
        self._clock = clock

    @property
    def engine(self) -> Engine:
        """Expose the shared persistence boundary to sibling Hub stores."""

        return self._engine

    def put(self, binding: Any) -> None:
        now = float(self._clock())
        row = WorkflowControlBindingDB(
            id=str(binding.workflow_id),
            tenant_id=str(binding.tenant_id),
            subject_id=str(binding.subject_id),
            workflow_id=str(binding.workflow_id),
            run_id=str(binding.run_id),
            runtime_id=str(binding.runtime_id),
            plan_hash=str(binding.plan_hash),
            policy_version=str(binding.policy_version),
            checkpoint_id=str(binding.checkpoint_id),
            workflow_request=deepcopy(binding.request.to_dict()),
            execution_plan=deepcopy(dict(binding.execution_plan or {})),
            last_status={},
            public_status={},
            runtime_revision=0,
            runtime_checkpoint_ref=str(binding.checkpoint_id),
            command_claim="",
            command_claim_expires_at=0.0,
            command_observation_pending=False,
            command_observation_min_revision=0,
            command_observation_expected_status="",
            dispatch_intent_id="",
            command_receipt_id="",
            scheduler_owner="",
            scheduler_lease_expires_at=0.0,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        try:
            with Session(self._engine) as session:
                session.add(row)
                session.commit()
        except IntegrityError as exc:
            raise WorkflowControlBindingPersistenceError("workflow_control_binding_already_exists") from exc

    def get(self, workflow_id: str):
        normalized = str(workflow_id or "").strip()
        if not normalized:
            return None
        with Session(self._engine) as session:
            row = session.get(WorkflowControlBindingDB, normalized)
            return self._binding(row) if row is not None else None

    def get_by_run_id(self, run_id: str):
        normalized = str(run_id or "").strip()
        if not normalized:
            return None
        with Session(self._engine) as session:
            row = session.exec(
                select(WorkflowControlBindingDB).where(WorkflowControlBindingDB.run_id == normalized)
            ).first()
            return self._binding(row) if row is not None else None

    def bind_runtime(self, workflow_id: str, *, plan_hash: str, runtime_id: str):
        normalized = str(workflow_id or "").strip()
        selected = str(runtime_id or "").strip()
        with Session(self._engine) as session:
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.plan_hash == str(plan_hash),
                    WorkflowControlBindingDB.runtime_id.in_(["pending", selected]),
                )
                .values(
                    runtime_id=selected,
                    revision=WorkflowControlBindingDB.revision + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError("workflow_control_runtime_binding_conflict")
            session.commit()
            row = session.get(WorkflowControlBindingDB, normalized)
            if row is None:
                raise WorkflowControlBindingPersistenceError("workflow_control_binding_not_found")
            return self._binding(row)

    def list_reconcilable(self, *, runtime_id: str, limit: int = 100) -> tuple[Any, ...]:
        bounded = max(1, min(int(limit), 1000))
        now = float(self._clock())
        with Session(self._engine) as session:
            rows = session.exec(
                select(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.runtime_id == str(runtime_id),
                    WorkflowControlBindingDB.dispatch_intent_id == "",
                    WorkflowControlBindingDB.command_receipt_id == "",
                    sa.or_(
                        WorkflowControlBindingDB.command_claim == "",
                        WorkflowControlBindingDB.command_claim_expires_at <= now,
                    ),
                )
                .order_by(WorkflowControlBindingDB.updated_at.asc())
                .limit(bounded * 4)
            ).all()
            values = [
                self._binding(row)
                for row in rows
                if row.last_status
                and (
                    row.command_observation_pending
                    or str((row.last_status or {}).get("status") or "").lower()
                    not in {"completed", "failed", "cancelled"}
                )
            ]
            return tuple(values[:bounded])

    def claim_reconcilable(
        self,
        *,
        runtime_id: str,
        owner_id: str,
        lease_seconds: float,
        limit: int = 100,
    ) -> tuple[Any, ...]:
        bounded = max(1, min(int(limit), 1000))
        now = float(self._clock())
        claimed: list[Any] = []
        with Session(self._engine) as session:
            rows = session.exec(
                select(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.runtime_id == str(runtime_id),
                    WorkflowControlBindingDB.dispatch_intent_id == "",
                    WorkflowControlBindingDB.command_receipt_id == "",
                    sa.or_(
                        WorkflowControlBindingDB.command_claim == "",
                        WorkflowControlBindingDB.command_claim_expires_at <= now,
                    ),
                    sa.or_(
                        WorkflowControlBindingDB.scheduler_owner == "",
                        WorkflowControlBindingDB.scheduler_lease_expires_at <= now,
                    ),
                )
                .order_by(WorkflowControlBindingDB.updated_at.asc())
                .limit(bounded * 4)
            ).all()
            for row in rows:
                if not row.last_status:
                    continue
                if not row.command_observation_pending and str((row.last_status or {}).get("status") or "").lower() in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    continue
                result = session.exec(
                    sa.update(WorkflowControlBindingDB)
                    .where(
                        WorkflowControlBindingDB.id == row.id,
                        WorkflowControlBindingDB.revision == int(row.revision),
                        WorkflowControlBindingDB.dispatch_intent_id == "",
                        WorkflowControlBindingDB.command_receipt_id == "",
                        sa.or_(
                            WorkflowControlBindingDB.command_claim == "",
                            WorkflowControlBindingDB.command_claim_expires_at <= now,
                        ),
                        sa.or_(
                            WorkflowControlBindingDB.scheduler_owner == "",
                            WorkflowControlBindingDB.scheduler_lease_expires_at <= now,
                        ),
                    )
                    .values(
                        scheduler_owner=str(owner_id),
                        scheduler_lease_expires_at=(now + max(1.0, float(lease_seconds))),
                        revision=int(row.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(result.rowcount or 0) == 1:
                    session.flush()
                    session.expire_all()
                    refreshed = session.get(WorkflowControlBindingDB, row.id)
                    if refreshed is not None:
                        claimed.append(self._binding(refreshed))
                if len(claimed) >= bounded:
                    break
            session.commit()
        return tuple(claimed)

    def finish_reconciliation(
        self,
        workflow_id: str,
        *,
        owner_id: str,
        expected_revision: int,
        expected_checkpoint_ref: str,
        status: dict[str, Any],
    ) -> None:
        normalized = str(workflow_id or "").strip()
        safe_status = deepcopy(status)
        with Session(self._engine) as session:
            row = session.get(WorkflowControlBindingDB, normalized)
            if row is None:
                raise WorkflowControlBindingPersistenceError("workflow_control_binding_not_found")
            _assert_raw_status_progression(row, safe_status)
            _assert_persisted_observation_fence(row, safe_status)
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.revision == int(row.revision),
                    WorkflowControlBindingDB.dispatch_intent_id == "",
                    WorkflowControlBindingDB.command_receipt_id == "",
                    WorkflowControlBindingDB.scheduler_owner == str(owner_id),
                    WorkflowControlBindingDB.runtime_revision == int(expected_revision),
                    WorkflowControlBindingDB.runtime_checkpoint_ref == str(expected_checkpoint_ref),
                    sa.or_(
                        WorkflowControlBindingDB.command_claim == "",
                        WorkflowControlBindingDB.command_observation_pending.is_(True),
                    ),
                )
                .values(
                    last_status=safe_status,
                    runtime_revision=_runtime_revision(safe_status),
                    runtime_checkpoint_ref=_checkpoint_ref(
                        safe_status,
                        fallback=str(expected_checkpoint_ref),
                    ),
                    scheduler_owner="",
                    scheduler_lease_expires_at=0.0,
                    command_claim="",
                    command_claim_expires_at=0.0,
                    command_observation_pending=False,
                    command_observation_min_revision=0,
                    command_observation_expected_status="",
                    revision=WorkflowControlBindingDB.revision + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError("workflow_control_reconciliation_cas_conflict")
            session.commit()

    def release_reconciliation(self, workflow_id: str, *, owner_id: str) -> None:
        with Session(self._engine) as session:
            session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == str(workflow_id or "").strip(),
                    WorkflowControlBindingDB.scheduler_owner == str(owner_id),
                )
                .values(
                    scheduler_owner="",
                    scheduler_lease_expires_at=0.0,
                    revision=WorkflowControlBindingDB.revision + 1,
                    updated_at=float(self._clock()),
                )
            )
            session.commit()

    def discard(self, workflow_id: str, *, plan_hash: str = "") -> None:
        normalized = str(workflow_id or "").strip()
        if not normalized:
            return
        statement = sa.delete(WorkflowControlBindingDB).where(WorkflowControlBindingDB.id == normalized)
        if plan_hash:
            statement = statement.where(WorkflowControlBindingDB.plan_hash == str(plan_hash))
        with Session(self._engine) as session:
            session.exec(statement)
            session.commit()

    def record_status(self, workflow_id: str, status: dict[str, Any]) -> None:
        normalized = str(workflow_id or "").strip()
        safe_status = deepcopy(status)
        with Session(self._engine) as session:
            row = session.get(WorkflowControlBindingDB, normalized)
            if row is None:
                raise WorkflowControlBindingPersistenceError("workflow_control_binding_not_found")
            _assert_raw_status_progression(row, safe_status)
            expected_revision = int(row.revision)
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.revision == expected_revision,
                    WorkflowControlBindingDB.command_claim == "",
                    WorkflowControlBindingDB.command_receipt_id == "",
                    WorkflowControlBindingDB.command_observation_pending.is_(False),
                )
                .values(
                    last_status=safe_status,
                    runtime_revision=_runtime_revision(safe_status),
                    runtime_checkpoint_ref=_checkpoint_ref(
                        safe_status,
                        fallback=str(row.runtime_checkpoint_ref),
                    ),
                    revision=expected_revision + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError("workflow_control_binding_revision_conflict")
            session.commit()

    def claim_command(
        self,
        workflow_id: str,
        *,
        expected_revision: int,
        checkpoint_id: str,
        command_id: str,
    ) -> None:
        normalized = str(workflow_id or "").strip()
        now = float(self._clock())
        with Session(self._engine) as session:
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.runtime_revision == int(expected_revision),
                    WorkflowControlBindingDB.runtime_checkpoint_ref == str(checkpoint_id),
                    WorkflowControlBindingDB.command_observation_pending.is_(False),
                    sa.or_(
                        WorkflowControlBindingDB.command_receipt_id == "",
                        WorkflowControlBindingDB.command_receipt_id == str(command_id),
                    ),
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
                    command_claim=str(command_id),
                    command_claim_expires_at=now + 300.0,
                    command_observation_pending=False,
                    command_observation_min_revision=0,
                    command_observation_expected_status="",
                    revision=WorkflowControlBindingDB.revision + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError("workflow_control_command_cas_conflict")
            session.commit()

    def bind_command_receipt(
        self,
        workflow_id: str,
        *,
        receipt_id: str,
        expected_revision: int,
        checkpoint_ref: str,
    ) -> None:
        normalized = str(workflow_id or "").strip()
        now = float(self._clock())
        with Session(self._engine) as session:
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.runtime_revision == int(expected_revision),
                    WorkflowControlBindingDB.runtime_checkpoint_ref == str(checkpoint_ref),
                    WorkflowControlBindingDB.dispatch_intent_id == "",
                    WorkflowControlBindingDB.command_receipt_id == "",
                    WorkflowControlBindingDB.command_claim == "",
                    WorkflowControlBindingDB.command_observation_pending.is_(False),
                    sa.or_(
                        WorkflowControlBindingDB.scheduler_owner == "",
                        WorkflowControlBindingDB.scheduler_lease_expires_at <= now,
                    ),
                )
                .values(
                    command_receipt_id=str(receipt_id),
                    revision=WorkflowControlBindingDB.revision + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError("workflow_control_command_receipt_stage_conflict")
            session.commit()

    def clear_command_receipt(self, workflow_id: str, *, receipt_id: str) -> None:
        normalized = str(workflow_id or "").strip()
        with Session(self._engine) as session:
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.command_receipt_id == str(receipt_id),
                    WorkflowControlBindingDB.command_claim == "",
                )
                .values(
                    command_receipt_id="",
                    revision=WorkflowControlBindingDB.revision + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError("workflow_control_command_receipt_completion_conflict")
            session.commit()

    def finish_command_receipt(
        self,
        workflow_id: str,
        *,
        receipt_id: str,
        status: dict[str, Any],
    ) -> None:
        normalized = str(workflow_id or "").strip()
        del status
        with Session(self._engine) as session:
            row = session.get(WorkflowControlBindingDB, normalized)
            if row is None:
                raise WorkflowControlBindingPersistenceError("workflow_control_binding_not_found")
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.revision == int(row.revision),
                    WorkflowControlBindingDB.command_receipt_id == str(receipt_id),
                    WorkflowControlBindingDB.command_claim == "",
                )
                .values(
                    command_receipt_id="",
                    revision=int(row.revision) + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError("workflow_control_command_receipt_completion_conflict")
            session.commit()

    def reject_command_receipt(self, workflow_id: str, *, receipt_id: str) -> None:
        self.clear_command_receipt(workflow_id, receipt_id=receipt_id)

    def finish_command(
        self,
        workflow_id: str,
        *,
        command_id: str,
        status: dict[str, Any],
    ) -> None:
        normalized = str(workflow_id or "").strip()
        safe_status = deepcopy(status)
        with Session(self._engine) as session:
            row = session.get(WorkflowControlBindingDB, normalized)
            if row is None:
                raise WorkflowControlBindingPersistenceError("workflow_control_binding_not_found")
            _assert_raw_status_progression(row, safe_status)
            _assert_persisted_observation_fence(row, safe_status)
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.revision == int(row.revision),
                    WorkflowControlBindingDB.command_claim == str(command_id),
                )
                .values(
                    last_status=safe_status,
                    runtime_revision=_runtime_revision(safe_status),
                    runtime_checkpoint_ref=_checkpoint_ref(
                        safe_status,
                        fallback=str(row.runtime_checkpoint_ref),
                    ),
                    command_claim="",
                    command_claim_expires_at=0.0,
                    command_observation_pending=False,
                    command_observation_min_revision=0,
                    command_observation_expected_status="",
                    revision=int(row.revision) + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError("workflow_control_command_finish_conflict")
            session.commit()

    def mark_command_observation_pending(
        self,
        workflow_id: str,
        *,
        command_id: str,
        minimum_revision: int,
        expected_status: str = "",
        reconciliation_ready: bool = True,
    ) -> None:
        normalized = str(workflow_id or "").strip()
        minimum = _pending_revision(minimum_revision)
        status = _pending_status(expected_status)
        ready = _pending_readiness(reconciliation_ready)
        with Session(self._engine) as session:
            row = session.get(WorkflowControlBindingDB, normalized)
            if row is None or row.command_claim != str(command_id):
                raise WorkflowControlBindingPersistenceError("workflow_control_command_pending_conflict")
            current_minimum = int(row.command_observation_min_revision or 0)
            current_status = str(row.command_observation_expected_status or "")
            if minimum < current_minimum and not ready:
                return
            if minimum == current_minimum and current_status and status and current_status != status:
                raise WorkflowControlBindingPersistenceError("workflow_control_command_pending_fence_conflict")
            if minimum < current_minimum:
                next_status = current_status
            elif minimum > current_minimum:
                next_status = status
            else:
                next_status = status or current_status
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.command_claim == str(command_id),
                    WorkflowControlBindingDB.revision == int(row.revision),
                )
                .values(
                    command_observation_pending=True,
                    command_observation_min_revision=max(current_minimum, minimum),
                    command_observation_expected_status=next_status,
                    command_claim_expires_at=(0.0 if ready else float(row.command_claim_expires_at)),
                    revision=int(row.revision) + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError("workflow_control_command_pending_conflict")
            session.commit()

    def release_command(self, workflow_id: str, *, command_id: str) -> None:
        normalized = str(workflow_id or "").strip()
        with Session(self._engine) as session:
            session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.command_claim == str(command_id),
                    WorkflowControlBindingDB.command_observation_pending.is_(False),
                )
                .values(
                    command_claim="",
                    command_claim_expires_at=0.0,
                    command_observation_min_revision=0,
                    command_observation_expected_status="",
                    revision=WorkflowControlBindingDB.revision + 1,
                    updated_at=float(self._clock()),
                )
            )
            session.commit()

    def last_status(self, workflow_id: str) -> dict[str, Any] | None:
        normalized = str(workflow_id or "").strip()
        if not normalized:
            return None
        with Session(self._engine) as session:
            row = session.get(WorkflowControlBindingDB, normalized)
            if row is None or not row.last_status:
                return None
            return deepcopy(dict(row.last_status))

    def record_public_status(self, workflow_id: str, status: dict[str, Any]) -> None:
        normalized = str(workflow_id or "").strip()
        safe_status = deepcopy(status)
        with Session(self._engine) as session:
            row = session.get(WorkflowControlBindingDB, normalized)
            if row is None:
                raise WorkflowControlBindingPersistenceError("workflow_control_binding_not_found")
            previous = dict(row.public_status or {}) or None
            _assert_public_status_progression(previous, safe_status)
            if previous == safe_status:
                return
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.revision == int(row.revision),
                )
                .values(
                    public_status=safe_status,
                    revision=int(row.revision) + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                with Session(self._engine) as adoption:
                    refreshed = adoption.get(WorkflowControlBindingDB, normalized)
                    if refreshed is not None and dict(refreshed.public_status or {}) == safe_status:
                        return
                raise WorkflowControlBindingPersistenceError("workflow_control_public_status_cas_conflict")
            session.commit()

    def last_public_status(self, workflow_id: str) -> dict[str, Any] | None:
        normalized = str(workflow_id or "").strip()
        if not normalized:
            return None
        with Session(self._engine) as session:
            row = session.get(WorkflowControlBindingDB, normalized)
            if row is None or not row.public_status:
                return None
            return deepcopy(dict(row.public_status))

    @staticmethod
    def _binding(row: WorkflowControlBindingDB):
        # The import stays local to avoid a composition/persistence module cycle.
        from agent.services.workflow_control_composition import WorkflowControlRunBinding

        return WorkflowControlRunBinding(
            tenant_id=str(row.tenant_id),
            subject_id=str(row.subject_id),
            workflow_id=str(row.workflow_id),
            run_id=str(row.run_id),
            runtime_id=str(row.runtime_id),
            plan_hash=str(row.plan_hash),
            policy_version=str(row.policy_version),
            checkpoint_id=str(row.checkpoint_id),
            request=WorkflowRequest.from_mapping(deepcopy(dict(row.workflow_request))),
            execution_plan=deepcopy(dict(row.execution_plan or {})),
        )


class SQLAlchemyWorkflowCommandReplayNonceStore:
    """Atomically consume hashed command nonces across Hub restarts."""

    def __init__(self, engine: Engine, *, clock=time.time) -> None:
        self._engine = engine
        self._clock = clock

    def consume(self, *, tenant_id: str, nonce: str, expires_at: float) -> bool:
        normalized_tenant = str(tenant_id or "").strip()
        normalized_nonce = str(nonce or "").strip()
        now = float(self._clock())
        if not normalized_tenant or not normalized_nonce or float(expires_at) <= now:
            return False
        nonce_hash = hashlib.sha256(normalized_nonce.encode("utf-8")).hexdigest()
        row_id = hashlib.sha256(f"{normalized_tenant}\0{nonce_hash}".encode("utf-8")).hexdigest()
        try:
            with Session(self._engine) as session:
                session.exec(sa.delete(WorkflowCommandNonceDB).where(WorkflowCommandNonceDB.expires_at <= now))
                session.add(
                    WorkflowCommandNonceDB(
                        id=row_id,
                        tenant_id=normalized_tenant,
                        nonce_hash=nonce_hash,
                        expires_at=float(expires_at),
                        consumed_at=now,
                    )
                )
                session.commit()
                return True
        except IntegrityError:
            return False


__all__ = [
    "SQLAlchemyWorkflowControlBindingStore",
    "SQLAlchemyWorkflowCommandReplayNonceStore",
    "WorkflowControlBindingPersistenceError",
]


def _runtime_revision(status: dict[str, Any]) -> int:
    if isinstance(status.get("revision"), bool):
        raise WorkflowControlBindingPersistenceError("workflow_control_runtime_revision_invalid")
    try:
        revision = int(status.get("revision", 0))
    except (TypeError, ValueError) as exc:
        raise WorkflowControlBindingPersistenceError("workflow_control_runtime_revision_invalid") from exc
    if revision < 0:
        raise WorkflowControlBindingPersistenceError("workflow_control_runtime_revision_invalid")
    return revision


def _checkpoint_ref(status: dict[str, Any], *, fallback: str) -> str:
    return str(status.get("checkpoint_ref") or fallback)


def _pending_revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkflowControlBindingPersistenceError("workflow_control_command_pending_revision_invalid")
    return value


def _pending_status(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 64:
        raise WorkflowControlBindingPersistenceError("workflow_control_command_pending_status_invalid")
    if any(not character.isprintable() or character in {"\x00", "\x7f"} for character in value):
        raise WorkflowControlBindingPersistenceError("workflow_control_command_pending_status_invalid")
    return value


def _pending_readiness(value: Any) -> bool:
    if not isinstance(value, bool):
        raise WorkflowControlBindingPersistenceError("workflow_control_command_pending_readiness_invalid")
    return value


def _assert_persisted_observation_fence(
    row: WorkflowControlBindingDB,
    status: dict[str, Any],
) -> None:
    if not row.command_observation_pending:
        return
    source = status.get("source_observation")
    if not isinstance(source, dict):
        raise WorkflowControlBindingPersistenceError("workflow_control_command_observation_fence_conflict")
    revision = source.get("revision")
    minimum = int(row.command_observation_min_revision or 0)
    expected = str(row.command_observation_expected_status or "")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < minimum
        or (revision == minimum and expected and source.get("status") != expected)
    ):
        raise WorkflowControlBindingPersistenceError("workflow_control_command_observation_fence_conflict")


def _assert_raw_status_progression(
    row: WorkflowControlBindingDB,
    status: dict[str, Any],
) -> None:
    if str(row.runtime_id) == "temporal":
        return
    previous = dict(row.last_status or {}) or None
    try:
        assert_runtime_status_progression(previous, status)
    except RuntimeError as exc:
        raise WorkflowControlBindingPersistenceError(str(exc)) from exc


def _assert_public_status_progression(
    previous: dict[str, Any] | None,
    status: dict[str, Any],
) -> None:
    try:
        assert_public_status_progression(previous, status)
    except RuntimeError as exc:
        raise WorkflowControlBindingPersistenceError(str(exc)) from exc
