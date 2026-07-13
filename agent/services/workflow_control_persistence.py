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
            runtime_revision=0,
            runtime_checkpoint_ref=str(binding.checkpoint_id),
            command_claim="",
            command_claim_expires_at=0.0,
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
            raise WorkflowControlBindingPersistenceError(
                "workflow_control_binding_already_exists"
            ) from exc

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
                select(WorkflowControlBindingDB).where(
                    WorkflowControlBindingDB.run_id == normalized
                )
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
                raise WorkflowControlBindingPersistenceError(
                    "workflow_control_runtime_binding_conflict"
                )
            session.commit()
            row = session.get(WorkflowControlBindingDB, normalized)
            if row is None:
                raise WorkflowControlBindingPersistenceError(
                    "workflow_control_binding_not_found"
                )
            return self._binding(row)

    def list_reconcilable(
        self, *, runtime_id: str, limit: int = 100
    ) -> tuple[Any, ...]:
        bounded = max(1, min(int(limit), 1000))
        now = float(self._clock())
        with Session(self._engine) as session:
            rows = session.exec(
                select(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.runtime_id == str(runtime_id),
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
                if str((row.last_status or {}).get("status") or "").lower()
                not in {"completed", "failed", "cancelled"}
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
                    sa.or_(
                        WorkflowControlBindingDB.command_claim == "",
                        WorkflowControlBindingDB.command_claim_expires_at <= now,
                    ),
                    sa.or_(
                        WorkflowControlBindingDB.scheduler_owner == "",
                        WorkflowControlBindingDB.scheduler_lease_expires_at <= now,
                        WorkflowControlBindingDB.scheduler_owner == str(owner_id),
                    ),
                )
                .order_by(WorkflowControlBindingDB.updated_at.asc())
                .limit(bounded * 4)
            ).all()
            for row in rows:
                if str((row.last_status or {}).get("status") or "").lower() in {
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
                        sa.or_(
                            WorkflowControlBindingDB.scheduler_owner == "",
                            WorkflowControlBindingDB.scheduler_lease_expires_at <= now,
                            WorkflowControlBindingDB.scheduler_owner == str(owner_id),
                        ),
                    )
                    .values(
                        scheduler_owner=str(owner_id),
                        scheduler_lease_expires_at=(
                            now + max(1.0, float(lease_seconds))
                        ),
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
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.scheduler_owner == str(owner_id),
                    WorkflowControlBindingDB.runtime_revision == int(expected_revision),
                    WorkflowControlBindingDB.runtime_checkpoint_ref
                    == str(expected_checkpoint_ref),
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
                    revision=WorkflowControlBindingDB.revision + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError(
                    "workflow_control_reconciliation_cas_conflict"
                )
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
        statement = sa.delete(WorkflowControlBindingDB).where(
            WorkflowControlBindingDB.id == normalized
        )
        if plan_hash:
            statement = statement.where(
                WorkflowControlBindingDB.plan_hash == str(plan_hash)
            )
        with Session(self._engine) as session:
            session.exec(statement)
            session.commit()

    def record_status(self, workflow_id: str, status: dict[str, Any]) -> None:
        normalized = str(workflow_id or "").strip()
        safe_status = deepcopy(status)
        with Session(self._engine) as session:
            row = session.get(WorkflowControlBindingDB, normalized)
            if row is None:
                raise WorkflowControlBindingPersistenceError(
                    "workflow_control_binding_not_found"
                )
            expected_revision = int(row.revision)
            result = session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.revision == expected_revision,
                    WorkflowControlBindingDB.command_claim == "",
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
                raise WorkflowControlBindingPersistenceError(
                    "workflow_control_binding_revision_conflict"
                )
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
                    sa.or_(
                        WorkflowControlBindingDB.command_claim == "",
                        WorkflowControlBindingDB.command_claim_expires_at <= now,
                    ),
                )
                .values(
                    command_claim=str(command_id),
                    command_claim_expires_at=now + 300.0,
                    revision=WorkflowControlBindingDB.revision + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError(
                    "workflow_control_command_cas_conflict"
                )
            session.commit()

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
                raise WorkflowControlBindingPersistenceError(
                    "workflow_control_binding_not_found"
                )
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
                    revision=int(row.revision) + 1,
                    updated_at=float(self._clock()),
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise WorkflowControlBindingPersistenceError(
                    "workflow_control_command_finish_conflict"
                )
            session.commit()

    def release_command(self, workflow_id: str, *, command_id: str) -> None:
        normalized = str(workflow_id or "").strip()
        with Session(self._engine) as session:
            session.exec(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == normalized,
                    WorkflowControlBindingDB.command_claim == str(command_id),
                )
                .values(
                    command_claim="",
                    command_claim_expires_at=0.0,
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
        row_id = hashlib.sha256(
            f"{normalized_tenant}\0{nonce_hash}".encode("utf-8")
        ).hexdigest()
        try:
            with Session(self._engine) as session:
                session.exec(
                    sa.delete(WorkflowCommandNonceDB).where(
                        WorkflowCommandNonceDB.expires_at <= now
                    )
                )
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
    try:
        revision = int(status.get("revision", 0))
    except (TypeError, ValueError) as exc:
        raise WorkflowControlBindingPersistenceError(
            "workflow_control_runtime_revision_invalid"
        ) from exc
    if revision < 0:
        raise WorkflowControlBindingPersistenceError(
            "workflow_control_runtime_revision_invalid"
        )
    return revision


def _checkpoint_ref(status: dict[str, Any], *, fallback: str) -> str:
    return str(status.get("checkpoint_ref") or fallback)
