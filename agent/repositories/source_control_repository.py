"""SQLModel adapter for canonical Hub-owned source-control persistence.

``SQLSourceControlRepository`` remains the single adapter that implements the
connection/revision, grant and index-lifecycle repository ports. It owns the
connection and revision tables itself and composes two focused stores for the
other ports (SRP): ``SQLSourceGrantStore`` and ``SQLSourceIndexLifecycleStore``.
Both are keyword-only overridable collaborators with production defaults, so
tests and alternative adapters can substitute them without subclassing.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_control import (
    SourceConnectionDB,
    SourceConnectionSelectorDB,
    SourceRevisionDB,
)
from agent.models.source_control_connection_binding import (
    SourceConnectionSelectorBinding,
)
from agent.models.source_control_persistence import (
    ActivationReconciliationResult,
    ActiveKnowledgeIndexEventRecord,
    ActiveKnowledgeIndexRecord,
    IndexLifecycleProjection,
    KnowledgeIndexBindingRecord,
    KnowledgeIndexRunBindingRecord,
    SourceAccessGrantAuditRecord,
    SourceAccessGrantPreview,
    SourceAccessGrantRecord,
    SourceConnectionRecord,
    SourceControlPersistenceError,
    SourceRevisionRecord,
)
from agent.repositories.source_control_grant_store import SQLSourceGrantStore
from agent.repositories.source_control_index_lifecycle_store import (
    SQLSourceIndexLifecycleStore,
)
from agent.repositories.source_control_record_mappers import (
    connection_record,
    new_connection_row,
    revision_record,
    selector_binding,
)
from ananta_contracts.source_control import (
    ConnectionState,
    GrantOperation,
    GrantState,
    GrantTransformation,
    SourceAccessGrant,
    SourceConnection,
    SourceRevision,
)

__all__ = [
    "SQLSourceControlRepository",
    "SQLSourceGrantStore",
    "SQLSourceIndexLifecycleStore",
]

_CONNECTION_TRANSITIONS = {
    "draft": frozenset({"active", "disabled", "tombstoned"}),
    "active": frozenset({"disabled", "tombstoned"}),
    "disabled": frozenset({"active", "tombstoned"}),
    "tombstoned": frozenset(),
}


class SQLSourceControlRepository:
    """One adapter implementing three segregated Hub repository ports."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], float] = time.time,
        activation_fault_hook: Callable[[], None] | None = None,
        grant_store: SQLSourceGrantStore | None = None,
        index_lifecycle_store: SQLSourceIndexLifecycleStore | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._grants = grant_store or SQLSourceGrantStore(engine, clock=clock)
        self._index_lifecycle = (
            index_lifecycle_store
            or SQLSourceIndexLifecycleStore(
                engine,
                clock=clock,
                activation_fault_hook=activation_fault_hook,
            )
        )

    def save_connection(
        self, contract: SourceConnection
    ) -> SourceConnectionRecord:
        with Session(self._engine) as db:
            existing = db.get(SourceConnectionDB, contract.connection_id)
            if existing is not None:
                record = connection_record(existing)
                if record.contract != contract:
                    raise SourceControlPersistenceError(
                        "source_control_connection_identity_conflict"
                    )
                return record
            now = float(self._clock())
            row = SourceConnectionDB(
                connection_id=contract.connection_id,
                tenant_id=contract.tenant_id,
                project_id=contract.project_id,
                owner_id=contract.owner_id,
                connector_type=contract.connector_type.value,
                connection_identity_digest=contract.connection_identity_digest,
                display_name=contract.display_name,
                sensitivity=contract.sensitivity.value,
                state=contract.state.value,
                lock_version=1,
                created_at_epoch=contract.created_at.timestamp(),
                updated_at_epoch=now,
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise SourceControlPersistenceError(
                    "source_control_connection_identity_conflict"
                ) from None
            db.refresh(row)
            return connection_record(row)

    def save_connection_with_selector(
        self,
        contract: SourceConnection,
        binding: SourceConnectionSelectorBinding,
    ) -> SourceConnectionRecord:
        """Atomically create or idempotently recover connection and binding."""

        if (
            binding.connection_id != contract.connection_id
            or binding.tenant_id != contract.tenant_id
            or binding.project_id != contract.project_id
            or binding.owner_id != contract.owner_id
            or binding.public_connector_type != contract.connector_type.value
        ):
            raise SourceControlPersistenceError(
                "source_control_connection_selector_scope_mismatch"
            )
        with Session(self._engine) as db:
            row = db.get(SourceConnectionDB, contract.connection_id)
            selector = db.get(
                SourceConnectionSelectorDB, contract.connection_id
            )
            if row is not None:
                record = connection_record(row)
                if record.contract != contract:
                    raise SourceControlPersistenceError(
                        "source_control_connection_identity_conflict"
                    )
                if selector is not None:
                    if selector_binding(selector) != binding:
                        raise SourceControlPersistenceError(
                            "source_control_connection_selector_conflict"
                        )
                    return record
            elif selector is not None:
                raise SourceControlPersistenceError(
                    "source_control_connection_selector_conflict"
                )
            now = float(self._clock())
            if row is None:
                row = new_connection_row(contract, now=now)
                db.add(row)
            db.add(
                SourceConnectionSelectorDB(
                    **binding.coordinates(),
                    binding_digest=binding.binding_digest,
                    created_at_epoch=now,
                )
            )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise SourceControlPersistenceError(
                    "source_control_connection_selector_conflict"
                ) from None
            db.refresh(row)
            return connection_record(row)

    def get_connection_selector(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
    ) -> SourceConnectionSelectorBinding | None:
        with Session(self._engine) as db:
            row = db.get(SourceConnectionSelectorDB, connection_id)
            if (
                row is None
                or row.tenant_id != tenant_id
                or row.project_id != project_id
            ):
                return None
            return selector_binding(row)

    def get_connection(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
    ) -> SourceConnectionRecord | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceConnectionDB).where(
                    SourceConnectionDB.connection_id == connection_id,
                    SourceConnectionDB.tenant_id == tenant_id,
                    SourceConnectionDB.project_id == project_id,
                    SourceConnectionDB.owner_id == owner_id,
                )
            ).first()
            return None if row is None else connection_record(row)

    def transition_connection(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
        target_state: ConnectionState,
        expected_lock_version: int,
    ) -> SourceConnectionRecord:
        target = target_state.value
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceConnectionDB).where(
                    SourceConnectionDB.connection_id == connection_id,
                    SourceConnectionDB.tenant_id == tenant_id,
                    SourceConnectionDB.project_id == project_id,
                    SourceConnectionDB.owner_id == owner_id,
                )
            ).first()
            if row is None:
                raise SourceControlPersistenceError(
                    "source_control_connection_not_found"
                )
            if row.lock_version != expected_lock_version:
                raise SourceControlPersistenceError(
                    "source_control_version_conflict"
                )
            if target not in _CONNECTION_TRANSITIONS[row.state]:
                raise SourceControlPersistenceError(
                    "source_control_connection_transition_invalid"
                )
            now = float(self._clock())
            values: dict[str, object] = {
                "state": target,
                "lock_version": expected_lock_version + 1,
                "updated_at_epoch": now,
            }
            if target == "disabled":
                values["disabled_at_epoch"] = now
            if target == "tombstoned":
                values["tombstoned_at_epoch"] = now
            result = db.execute(
                update(SourceConnectionDB)
                .where(
                    SourceConnectionDB.connection_id == connection_id,
                    SourceConnectionDB.lock_version == expected_lock_version,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                db.rollback()
                raise SourceControlPersistenceError(
                    "source_control_version_conflict"
                )
            db.commit()
            refreshed = db.get(SourceConnectionDB, connection_id)
            if refreshed is None:
                raise SourceControlPersistenceError(
                    "source_control_connection_not_found"
                )
            return connection_record(refreshed)

    def append_revision(
        self, contract: SourceRevision
    ) -> SourceRevisionRecord:
        with Session(self._engine) as db:
            connection = db.exec(
                select(SourceConnectionDB).where(
                    SourceConnectionDB.connection_id
                    == contract.connection_id,
                    SourceConnectionDB.tenant_id == contract.tenant_id,
                    SourceConnectionDB.project_id == contract.project_id,
                    SourceConnectionDB.owner_id == contract.owner_id,
                )
            ).first()
            if connection is None:
                raise SourceControlPersistenceError(
                    "source_control_connection_not_found"
                )
            existing = db.get(
                SourceRevisionDB,
                contract.source_revision_id,
            )
            if existing is not None:
                record = revision_record(existing)
                if record.contract != contract:
                    raise SourceControlPersistenceError(
                        "source_control_revision_append_conflict"
                    )
                return record
            row = SourceRevisionDB(
                source_revision_id=contract.source_revision_id,
                connection_id=contract.connection_id,
                tenant_id=contract.tenant_id,
                project_id=contract.project_id,
                owner_id=contract.owner_id,
                connector_type=contract.connector_type.value,
                sensitivity=contract.sensitivity.value,
                revision_token=contract.revision_token,
                revision_digest=contract.revision_digest,
                content_manifest_id=contract.content_manifest_id,
                content_manifest_digest=contract.content_manifest_digest,
                admission_state=contract.admission_state.value,
                captured_at_epoch=contract.captured_at.timestamp(),
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise SourceControlPersistenceError(
                    "source_control_revision_append_conflict"
                ) from None
            db.refresh(row)
            return revision_record(row)

    def get_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        source_revision_id: str,
    ) -> SourceRevisionRecord | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceRevisionDB).where(
                    SourceRevisionDB.source_revision_id
                    == source_revision_id,
                    SourceRevisionDB.tenant_id == tenant_id,
                    SourceRevisionDB.project_id == project_id,
                    SourceRevisionDB.owner_id == owner_id,
                )
            ).first()
            return None if row is None else revision_record(row)

    def get_scoped_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
    ) -> SourceRevisionRecord | None:
        """Resolve immutable revision ownership within an explicit scope."""

        with Session(self._engine) as db:
            row = db.exec(
                select(SourceRevisionDB).where(
                    SourceRevisionDB.source_revision_id
                    == source_revision_id,
                    SourceRevisionDB.tenant_id == tenant_id,
                    SourceRevisionDB.project_id == project_id,
                )
            ).first()
            return None if row is None else revision_record(row)

    # -- grant port: delegated to SQLSourceGrantStore ------------------

    def save_grant(
        self,
        contract: SourceAccessGrant,
        *,
        owner_id: str,
        grant_family_id: str,
        rollback_of_grant_id: str | None = None,
    ) -> SourceAccessGrantRecord:
        return self._grants.save_grant(
            contract,
            owner_id=owner_id,
            grant_family_id=grant_family_id,
            rollback_of_grant_id=rollback_of_grant_id,
        )

    def preview_grant(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        grant_id: str,
        source_revision_id: str,
        destination_id: str,
        operation: GrantOperation,
        transformation: GrantTransformation,
        at_epoch: float,
    ) -> SourceAccessGrantPreview:
        return self._grants.preview_grant(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            grant_id=grant_id,
            source_revision_id=source_revision_id,
            destination_id=destination_id,
            operation=operation,
            transformation=transformation,
            at_epoch=at_epoch,
        )

    def transition_grant(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        grant_id: str,
        target_state: GrantState,
        expected_lock_version: int,
        reason_code: str,
    ) -> SourceAccessGrantRecord:
        return self._grants.transition_grant(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            grant_id=grant_id,
            target_state=target_state,
            expected_lock_version=expected_lock_version,
            reason_code=reason_code,
        )

    def rollback_grant(
        self,
        *,
        previous_grant_id: str,
        replacement: SourceAccessGrant,
        owner_id: str,
        grant_family_id: str,
        expected_previous_lock_version: int,
        reason_code: str,
    ) -> SourceAccessGrantRecord:
        return self._grants.rollback_grant(
            previous_grant_id=previous_grant_id,
            replacement=replacement,
            owner_id=owner_id,
            grant_family_id=grant_family_id,
            expected_previous_lock_version=expected_previous_lock_version,
            reason_code=reason_code,
        )

    def list_grant_audit(
        self, *, grant_id: str
    ) -> tuple[SourceAccessGrantAuditRecord, ...]:
        return self._grants.list_grant_audit(grant_id=grant_id)

    # -- index lifecycle port: delegated to SQLSourceIndexLifecycleStore

    def save_index_binding(
        self, record: KnowledgeIndexBindingRecord
    ) -> KnowledgeIndexBindingRecord:
        return self._index_lifecycle.save_index_binding(record)

    def save_index_run_binding(
        self, record: KnowledgeIndexRunBindingRecord
    ) -> KnowledgeIndexRunBindingRecord:
        return self._index_lifecycle.save_index_run_binding(record)

    def project_completed_index_run(
        self,
        *,
        index: KnowledgeIndexBindingRecord,
        run: KnowledgeIndexRunBindingRecord,
    ) -> tuple[KnowledgeIndexBindingRecord, KnowledgeIndexRunBindingRecord]:
        """Atomically insert or replay one fully verified completed run."""

        return self._index_lifecycle.project_completed_index_run(
            index=index,
            run=run,
        )

    def complete_index_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        index_run_id: str,
        expected_run_lock_version: int,
        expected_index_lock_version: int,
        artifact_manifest_digest: str,
        completed_at_epoch: float,
    ) -> tuple[
        KnowledgeIndexBindingRecord,
        KnowledgeIndexRunBindingRecord,
    ]:
        return self._index_lifecycle.complete_index_run(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            index_run_id=index_run_id,
            expected_run_lock_version=expected_run_lock_version,
            expected_index_lock_version=expected_index_lock_version,
            artifact_manifest_digest=artifact_manifest_digest,
            completed_at_epoch=completed_at_epoch,
        )

    def activate_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
        knowledge_index_id: str,
        current_source_revision_id: str,
        current_policy_snapshot_digest: str,
        expected_generation: int,
        action: str,
    ) -> ActiveKnowledgeIndexRecord:
        return self._index_lifecycle.activate_index(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            connection_id=connection_id,
            knowledge_index_id=knowledge_index_id,
            current_source_revision_id=current_source_revision_id,
            current_policy_snapshot_digest=current_policy_snapshot_digest,
            expected_generation=expected_generation,
            action=action,
        )

    def get_active_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
    ) -> ActiveKnowledgeIndexRecord | None:
        return self._index_lifecycle.get_active_index(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            connection_id=connection_id,
        )

    def project_index_lifecycle(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
        knowledge_index_id: str,
        current_source_revision_id: str,
        current_policy_snapshot_digest: str,
    ) -> IndexLifecycleProjection:
        return self._index_lifecycle.project_index_lifecycle(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            connection_id=connection_id,
            knowledge_index_id=knowledge_index_id,
            current_source_revision_id=current_source_revision_id,
            current_policy_snapshot_digest=current_policy_snapshot_digest,
        )

    def reconcile_activation(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
        current_source_revision_id: str,
        current_policy_snapshot_digest: str,
    ) -> ActivationReconciliationResult:
        return self._index_lifecycle.reconcile_activation(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            connection_id=connection_id,
            current_source_revision_id=current_source_revision_id,
            current_policy_snapshot_digest=current_policy_snapshot_digest,
        )

    def list_activation_events(
        self, *, active_index_id: str
    ) -> tuple[ActiveKnowledgeIndexEventRecord, ...]:
        return self._index_lifecycle.list_activation_events(
            active_index_id=active_index_id
        )
