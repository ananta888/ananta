"""SQLModel adapter for canonical Hub-owned source-control persistence."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    ActiveKnowledgeIndexEventDB,
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantAuditDB,
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceConnectionSelectorDB,
    SourceControlJobEventOutboxDB,
    SourceRevisionDB,
)
from agent.services.source_control_persistence import (
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
    derive_active_index_id,
    derive_grant_family_id,
    derive_index_lifecycle,
)
from agent.services.source_control_connection_binding import (
    SourceConnectionSelectorBinding,
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

_CONNECTION_TRANSITIONS = {
    "draft": frozenset({"active", "disabled", "tombstoned"}),
    "active": frozenset({"disabled", "tombstoned"}),
    "disabled": frozenset({"active", "tombstoned"}),
    "tombstoned": frozenset(),
}
_GRANT_TRANSITIONS = {
    "draft": frozenset({"active", "revoked"}),
    "active": frozenset({"superseded", "revoked"}),
    "superseded": frozenset(),
    "revoked": frozenset(),
}


def _stable_id(prefix: str, coordinates: dict[str, object]) -> str:
    canonical = json.dumps(
        coordinates,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"{prefix}_{hashlib.sha256(canonical).hexdigest()}"


def _datetime(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


class SQLSourceControlRepository:
    """One adapter implementing three segregated Hub repository ports."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], float] = time.time,
        activation_fault_hook: Callable[[], None] | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._activation_fault_hook = activation_fault_hook

    def save_connection(
        self, contract: SourceConnection
    ) -> SourceConnectionRecord:
        with Session(self._engine) as db:
            existing = db.get(SourceConnectionDB, contract.connection_id)
            if existing is not None:
                record = self._connection_record(existing)
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
            return self._connection_record(row)

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
                record = self._connection_record(row)
                if record.contract != contract:
                    raise SourceControlPersistenceError(
                        "source_control_connection_identity_conflict"
                    )
                if selector is not None:
                    if self._selector_binding(selector) != binding:
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
                row = self._new_connection_row(contract, now=now)
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
            return self._connection_record(row)

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
            return self._selector_binding(row)

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
            return None if row is None else self._connection_record(row)

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
            return self._connection_record(refreshed)

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
                record = self._revision_record(existing)
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
            return self._revision_record(row)

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
            return None if row is None else self._revision_record(row)

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
            return None if row is None else self._revision_record(row)

    def save_grant(
        self,
        contract: SourceAccessGrant,
        *,
        owner_id: str,
        grant_family_id: str,
        rollback_of_grant_id: str | None = None,
    ) -> SourceAccessGrantRecord:
        with Session(self._engine) as db:
            with db.begin():
                existing = db.get(SourceAccessGrantDB, contract.grant_id)
                if existing is not None:
                    record = self._grant_record(existing)
                    if (
                        record.contract != contract
                        or record.owner_id != owner_id
                        or record.grant_family_id != grant_family_id
                    ):
                        raise SourceControlPersistenceError(
                            "source_control_grant_identity_conflict"
                        )
                    return record
                self._require_scoped_revision(
                    db,
                    tenant_id=contract.tenant_id,
                    project_id=contract.project_id,
                    owner_id=owner_id,
                    source_revision_id=contract.source_revision_id,
                )
                latest = db.exec(
                    select(SourceAccessGrantDB)
                    .where(
                        SourceAccessGrantDB.grant_family_id
                        == grant_family_id
                    )
                    .order_by(SourceAccessGrantDB.grant_version.desc())
                ).first()
                required_version = 1 if latest is None else latest.grant_version + 1
                if contract.version != required_version:
                    raise SourceControlPersistenceError(
                        "source_control_grant_version_invalid"
                    )
                now = float(self._clock())
                row = self._grant_row(
                    contract,
                    owner_id=owner_id,
                    grant_family_id=grant_family_id,
                    rollback_of_grant_id=rollback_of_grant_id,
                    updated_at_epoch=now,
                )
                db.add(row)
                self._add_grant_audit(
                    db,
                    row=row,
                    action="create",
                    from_state=None,
                    to_state=contract.state.value,
                    reason_code="grant_created",
                    grant_lock_version=1,
                    occurred_at_epoch=now,
                )
            return self._grant_record(row)

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
        with Session(self._engine) as db:
            with db.begin():
                row = self._require_scoped_grant(
                    db,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    owner_id=owner_id,
                    grant_id=grant_id,
                )
                bindings_match = (
                    row.source_revision_id == source_revision_id
                    and row.destination_id == destination_id
                    and row.operation == operation.value
                    and row.transformation == transformation.value
                )
                active_state = row.state in {"draft", "active"}
                unexpired = row.expires_at_epoch > at_epoch
                allowed = bindings_match and active_state and unexpired
                if not bindings_match:
                    reason = "grant_binding_mismatch"
                elif not active_state:
                    reason = "grant_not_usable"
                elif not unexpired:
                    reason = "grant_expired"
                else:
                    reason = "grant_preview_allowed"
                self._add_grant_audit(
                    db,
                    row=row,
                    action="preview",
                    from_state=row.state,
                    to_state=row.state,
                    reason_code=reason,
                    grant_lock_version=row.lock_version,
                    occurred_at_epoch=at_epoch,
                )
            return SourceAccessGrantPreview(
                grant_id=row.grant_id,
                allowed=allowed,
                reason_code=reason,
                source_revision_id=source_revision_id,
                destination_id=destination_id,
                operation=operation.value,
                transformation=transformation.value,
                lock_version=row.lock_version,
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
        target = target_state.value
        with Session(self._engine) as db:
            with db.begin():
                row = self._require_scoped_grant(
                    db,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    owner_id=owner_id,
                    grant_id=grant_id,
                )
                if row.lock_version != expected_lock_version:
                    raise SourceControlPersistenceError(
                        "source_control_version_conflict"
                    )
                if target not in _GRANT_TRANSITIONS[row.state]:
                    raise SourceControlPersistenceError(
                        "source_control_grant_transition_invalid"
                    )
                previous = row.state
                now = float(self._clock())
                result = db.execute(
                    update(SourceAccessGrantDB)
                    .where(
                        SourceAccessGrantDB.grant_id == grant_id,
                        SourceAccessGrantDB.lock_version
                        == expected_lock_version,
                    )
                    .values(
                        state=target,
                        lock_version=expected_lock_version + 1,
                        updated_at_epoch=now,
                    )
                )
                if result.rowcount != 1:
                    raise SourceControlPersistenceError(
                        "source_control_version_conflict"
                    )
                row.state = target
                row.lock_version = expected_lock_version + 1
                row.updated_at_epoch = now
                self._add_grant_audit(
                    db,
                    row=row,
                    action=target,
                    from_state=previous,
                    to_state=target,
                    reason_code=reason_code,
                    grant_lock_version=row.lock_version,
                    occurred_at_epoch=now,
                )
            return self._grant_record(row)

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
        with Session(self._engine) as db:
            with db.begin():
                previous = self._require_scoped_grant(
                    db,
                    tenant_id=replacement.tenant_id,
                    project_id=replacement.project_id,
                    owner_id=owner_id,
                    grant_id=previous_grant_id,
                )
                if (
                    previous.lock_version != expected_previous_lock_version
                    or previous.state not in {"superseded", "revoked"}
                    or previous.grant_family_id != grant_family_id
                    or derive_grant_family_id(replacement)
                    != grant_family_id
                    or replacement.state is not GrantState.ACTIVE
                ):
                    raise SourceControlPersistenceError(
                        "source_control_grant_rollback_invalid"
                    )
                latest = db.exec(
                    select(SourceAccessGrantDB)
                    .where(
                        SourceAccessGrantDB.grant_family_id
                        == grant_family_id
                    )
                    .order_by(SourceAccessGrantDB.grant_version.desc())
                ).first()
                if (
                    latest is None
                    or replacement.version != latest.grant_version + 1
                ):
                    raise SourceControlPersistenceError(
                        "source_control_grant_version_invalid"
                    )
                self._require_scoped_revision(
                    db,
                    tenant_id=replacement.tenant_id,
                    project_id=replacement.project_id,
                    owner_id=owner_id,
                    source_revision_id=replacement.source_revision_id,
                )
                now = float(self._clock())
                row = self._grant_row(
                    replacement,
                    owner_id=owner_id,
                    grant_family_id=grant_family_id,
                    rollback_of_grant_id=previous_grant_id,
                    updated_at_epoch=now,
                )
                db.add(row)
                self._add_grant_audit(
                    db,
                    row=row,
                    action="rollback",
                    from_state=previous.state,
                    to_state="active",
                    reason_code=reason_code,
                    grant_lock_version=1,
                    occurred_at_epoch=now,
                )
            return self._grant_record(row)

    def list_grant_audit(
        self, *, grant_id: str
    ) -> tuple[SourceAccessGrantAuditRecord, ...]:
        with Session(self._engine) as db:
            rows = db.exec(
                select(SourceAccessGrantAuditDB)
                .where(SourceAccessGrantAuditDB.grant_id == grant_id)
                .order_by(
                    SourceAccessGrantAuditDB.occurred_at_epoch,
                    SourceAccessGrantAuditDB.audit_id,
                )
            ).all()
            records = tuple(self._grant_audit_record(row) for row in rows)
            return tuple(
                sorted(
                    records,
                    key=lambda record: (
                        record.grant_lock_version,
                        1 if record.action == "preview" else 0,
                        record.occurred_at_epoch,
                        record.audit_id,
                    ),
                )
            )

    def save_index_binding(
        self, record: KnowledgeIndexBindingRecord
    ) -> KnowledgeIndexBindingRecord:
        with Session(self._engine) as db:
            existing = db.get(
                KnowledgeIndexSourceBindingDB,
                record.knowledge_index_id,
            )
            if existing is not None:
                stored = self._index_record(existing)
                if stored != record:
                    raise SourceControlPersistenceError(
                        "source_control_index_binding_conflict"
                    )
                return stored
            self._require_scoped_revision(
                db,
                tenant_id=record.tenant_id,
                project_id=record.project_id,
                owner_id=record.owner_id,
                source_revision_id=record.source_revision_id,
            )
            row = KnowledgeIndexSourceBindingDB(**record.__dict__)
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise SourceControlPersistenceError(
                    "source_control_index_binding_conflict"
                ) from None
            db.refresh(row)
            return self._index_record(row)

    def save_index_run_binding(
        self, record: KnowledgeIndexRunBindingRecord
    ) -> KnowledgeIndexRunBindingRecord:
        with Session(self._engine) as db:
            existing = db.get(
                KnowledgeIndexRunSourceBindingDB,
                record.index_run_id,
            )
            if existing is not None:
                stored = self._run_record(existing)
                if stored != record:
                    raise SourceControlPersistenceError(
                        "source_control_index_run_binding_conflict"
                    )
                return stored
            index = self._require_scoped_index(
                db,
                tenant_id=record.tenant_id,
                project_id=record.project_id,
                owner_id=record.owner_id,
                knowledge_index_id=record.knowledge_index_id,
            )
            if (
                index.source_revision_id != record.source_revision_id
                or index.policy_snapshot_id != record.policy_snapshot_id
                or index.policy_snapshot_digest
                != record.policy_snapshot_digest
            ):
                raise SourceControlPersistenceError(
                    "source_control_index_run_binding_mismatch"
                )
            row = KnowledgeIndexRunSourceBindingDB(**record.__dict__)
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise SourceControlPersistenceError(
                    "source_control_index_run_binding_conflict"
                ) from None
            db.refresh(row)
            return self._run_record(row)

    def project_completed_index_run(
        self,
        *,
        index: KnowledgeIndexBindingRecord,
        run: KnowledgeIndexRunBindingRecord,
    ) -> tuple[KnowledgeIndexBindingRecord, KnowledgeIndexRunBindingRecord]:
        """Atomically insert or replay one fully verified completed run."""

        if (
            index.status != "completed"
            or run.status != "completed"
            or not run.artifacts_verified
            or not index.activation_requested
            or not index.artifact_manifest_digest
            or run.artifact_manifest_digest
            != index.artifact_manifest_digest
            or run.knowledge_index_id != index.knowledge_index_id
            or (
                run.tenant_id,
                run.project_id,
                run.owner_id,
                run.source_revision_id,
                run.policy_snapshot_id,
                run.policy_snapshot_digest,
            )
            != (
                index.tenant_id,
                index.project_id,
                index.owner_id,
                index.source_revision_id,
                index.policy_snapshot_id,
                index.policy_snapshot_digest,
            )
        ):
            raise SourceControlPersistenceError(
                "source_control_completed_index_projection_invalid"
            )
        self._require_digest(index.artifact_manifest_digest)
        with Session(self._engine) as db:
            with db.begin():
                self._require_scoped_revision(
                    db,
                    tenant_id=index.tenant_id,
                    project_id=index.project_id,
                    owner_id=index.owner_id,
                    source_revision_id=index.source_revision_id,
                )
                index_row = db.get(
                    KnowledgeIndexSourceBindingDB,
                    index.knowledge_index_id,
                )
                if index_row is None:
                    index_row = KnowledgeIndexSourceBindingDB(
                        **index.__dict__
                    )
                    db.add(index_row)
                    db.flush()
                else:
                    self._assert_index_projection_binding(index_row, index)

                run_row = db.get(
                    KnowledgeIndexRunSourceBindingDB,
                    run.index_run_id,
                )
                if run_row is not None:
                    self._assert_run_projection_binding(run_row, run)
                    if run_row.status == "completed":
                        if (
                            not run_row.artifacts_verified
                            or run_row.artifact_manifest_digest
                            != run.artifact_manifest_digest
                        ):
                            raise SourceControlPersistenceError(
                                "source_control_index_run_projection_conflict"
                            )
                        return (
                            self._index_record(index_row),
                            self._run_record(run_row),
                        )
                    if run_row.status != "pending":
                        raise SourceControlPersistenceError(
                            "source_control_index_run_projection_conflict"
                        )
                    run_row.status = "completed"
                    run_row.artifact_manifest_digest = (
                        run.artifact_manifest_digest
                    )
                    run_row.artifacts_verified = True
                    run_row.lock_version += 1
                    run_row.completed_at_epoch = run.completed_at_epoch
                else:
                    run_row = KnowledgeIndexRunSourceBindingDB(
                        **run.__dict__
                    )
                    db.add(run_row)

                if index_row.status not in {"pending", "completed"}:
                    raise SourceControlPersistenceError(
                        "source_control_index_projection_conflict"
                    )
                if index_row.status == "completed":
                    index_row.lock_version += 1
                index_row.status = "completed"
                index_row.artifact_manifest_digest = (
                    index.artifact_manifest_digest
                )
                index_row.activation_requested = True
                index_row.updated_at_epoch = index.updated_at_epoch
                db.flush()
                projected_index = self._index_record(index_row)
                projected_run = self._run_record(run_row)
            return projected_index, projected_run

    @staticmethod
    def _assert_index_projection_binding(
        row: KnowledgeIndexSourceBindingDB,
        expected: KnowledgeIndexBindingRecord,
    ) -> None:
        fields = (
            "tenant_id",
            "project_id",
            "owner_id",
            "connection_id",
            "source_revision_id",
            "policy_snapshot_id",
            "policy_snapshot_digest",
            "index_contract_version",
        )
        if any(
            getattr(row, field) != getattr(expected, field)
            for field in fields
        ):
            raise SourceControlPersistenceError(
                "source_control_index_projection_binding_mismatch"
            )

    @staticmethod
    def _assert_run_projection_binding(
        row: KnowledgeIndexRunSourceBindingDB,
        expected: KnowledgeIndexRunBindingRecord,
    ) -> None:
        fields = (
            "knowledge_index_id",
            "tenant_id",
            "project_id",
            "owner_id",
            "source_revision_id",
            "policy_snapshot_id",
            "policy_snapshot_digest",
        )
        if any(
            getattr(row, field) != getattr(expected, field)
            for field in fields
        ):
            raise SourceControlPersistenceError(
                "source_control_index_run_projection_binding_mismatch"
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
        self._require_digest(artifact_manifest_digest)
        with Session(self._engine) as db:
            with db.begin():
                run = db.exec(
                    select(KnowledgeIndexRunSourceBindingDB).where(
                        KnowledgeIndexRunSourceBindingDB.index_run_id
                        == index_run_id,
                        KnowledgeIndexRunSourceBindingDB.tenant_id
                        == tenant_id,
                        KnowledgeIndexRunSourceBindingDB.project_id
                        == project_id,
                        KnowledgeIndexRunSourceBindingDB.owner_id == owner_id,
                    )
                ).first()
                if run is None:
                    raise SourceControlPersistenceError(
                        "source_control_index_run_not_found"
                    )
                index = self._require_scoped_index(
                    db,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    owner_id=owner_id,
                    knowledge_index_id=run.knowledge_index_id,
                )
                if (
                    run.lock_version != expected_run_lock_version
                    or index.lock_version != expected_index_lock_version
                ):
                    raise SourceControlPersistenceError(
                        "source_control_version_conflict"
                    )
                run_result = db.execute(
                    update(KnowledgeIndexRunSourceBindingDB)
                    .where(
                        KnowledgeIndexRunSourceBindingDB.index_run_id
                        == index_run_id,
                        KnowledgeIndexRunSourceBindingDB.lock_version
                        == expected_run_lock_version,
                    )
                    .values(
                        status="completed",
                        artifact_manifest_digest=artifact_manifest_digest,
                        artifacts_verified=True,
                        lock_version=expected_run_lock_version + 1,
                        completed_at_epoch=completed_at_epoch,
                    )
                )
                index_result = db.execute(
                    update(KnowledgeIndexSourceBindingDB)
                    .where(
                        KnowledgeIndexSourceBindingDB.knowledge_index_id
                        == index.knowledge_index_id,
                        KnowledgeIndexSourceBindingDB.lock_version
                        == expected_index_lock_version,
                    )
                    .values(
                        status="completed",
                        artifact_manifest_digest=artifact_manifest_digest,
                        activation_requested=True,
                        lock_version=expected_index_lock_version + 1,
                        updated_at_epoch=completed_at_epoch,
                    )
                )
                if run_result.rowcount != 1 or index_result.rowcount != 1:
                    raise SourceControlPersistenceError(
                        "source_control_version_conflict"
                    )
            with Session(self._engine) as loaded:
                refreshed_run = loaded.get(
                    KnowledgeIndexRunSourceBindingDB,
                    index_run_id,
                )
                refreshed_index = loaded.get(
                    KnowledgeIndexSourceBindingDB,
                    run.knowledge_index_id,
                )
                if refreshed_run is None or refreshed_index is None:
                    raise SourceControlPersistenceError(
                        "source_control_index_completion_inconsistent"
                    )
                return (
                    self._index_record(refreshed_index),
                    self._run_record(refreshed_run),
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
        if action not in {"activate", "rollback", "reconcile"}:
            raise SourceControlPersistenceError(
                "source_control_activation_action_invalid"
            )
        self._require_digest(current_policy_snapshot_digest)
        active_index_id = derive_active_index_id(
            tenant_id=tenant_id,
            project_id=project_id,
            connection_id=connection_id,
        )
        try:
            with Session(self._engine) as db:
                with db.begin():
                    index = self._require_scoped_index(
                        db,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        owner_id=owner_id,
                        knowledge_index_id=knowledge_index_id,
                    )
                    if (
                        index.connection_id != connection_id
                        or index.status != "completed"
                        or index.source_revision_id
                        != current_source_revision_id
                        or index.policy_snapshot_digest
                        != current_policy_snapshot_digest
                    ):
                        raise SourceControlPersistenceError(
                            "source_control_index_activation_stale"
                        )
                    verified_run = db.exec(
                        select(KnowledgeIndexRunSourceBindingDB).where(
                            KnowledgeIndexRunSourceBindingDB.knowledge_index_id
                            == knowledge_index_id,
                            KnowledgeIndexRunSourceBindingDB.status
                            == "completed",
                            KnowledgeIndexRunSourceBindingDB.artifacts_verified
                            == True,  # noqa: E712
                            KnowledgeIndexRunSourceBindingDB.artifact_manifest_digest
                            == index.artifact_manifest_digest,
                        )
                    ).first()
                    if verified_run is None:
                        raise SourceControlPersistenceError(
                            "source_control_index_artifacts_unverified"
                        )
                    active = db.get(ActiveKnowledgeIndexDB, active_index_id)
                    if active is None:
                        if expected_generation != 0:
                            raise SourceControlPersistenceError(
                                "source_control_generation_conflict"
                            )
                        generation = 1
                        previous_id = None
                        active = ActiveKnowledgeIndexDB(
                            active_index_id=active_index_id,
                            tenant_id=tenant_id,
                            project_id=project_id,
                            owner_id=owner_id,
                            connection_id=connection_id,
                            source_revision_id=index.source_revision_id,
                            policy_snapshot_digest=index.policy_snapshot_digest,
                            knowledge_index_id=knowledge_index_id,
                            previous_knowledge_index_id=None,
                            generation=generation,
                            updated_at_epoch=float(self._clock()),
                        )
                        db.add(active)
                        db.flush()
                    else:
                        if active.generation != expected_generation:
                            raise SourceControlPersistenceError(
                                "source_control_generation_conflict"
                            )
                        if active.knowledge_index_id == knowledge_index_id:
                            db.execute(
                                update(KnowledgeIndexSourceBindingDB)
                                .where(
                                    KnowledgeIndexSourceBindingDB.knowledge_index_id
                                    == knowledge_index_id
                                )
                                .values(activation_requested=False)
                            )
                            return self._active_record(active)
                        previous_id = active.knowledge_index_id
                        generation = expected_generation + 1
                        result = db.execute(
                            update(ActiveKnowledgeIndexDB)
                            .where(
                                ActiveKnowledgeIndexDB.active_index_id
                                == active_index_id,
                                ActiveKnowledgeIndexDB.generation
                                == expected_generation,
                            )
                            .values(
                                source_revision_id=index.source_revision_id,
                                policy_snapshot_digest=index.policy_snapshot_digest,
                                knowledge_index_id=knowledge_index_id,
                                previous_knowledge_index_id=previous_id,
                                generation=generation,
                                updated_at_epoch=float(self._clock()),
                            )
                        )
                        if result.rowcount != 1:
                            raise SourceControlPersistenceError(
                                "source_control_generation_conflict"
                            )
                    db.execute(
                        update(KnowledgeIndexSourceBindingDB)
                        .where(
                            KnowledgeIndexSourceBindingDB.knowledge_index_id
                            == knowledge_index_id
                        )
                        .values(activation_requested=False)
                    )
                    if self._activation_fault_hook is not None:
                        self._activation_fault_hook()
                    event = ActiveKnowledgeIndexEventDB(
                        event_id=_stable_id(
                            "event",
                            {
                                "action": action,
                                "active_index_id": active_index_id,
                                "generation": generation,
                                "knowledge_index_id": knowledge_index_id,
                            },
                        ),
                        active_index_id=active_index_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        connection_id=connection_id,
                        action=action,
                        from_knowledge_index_id=previous_id,
                        to_knowledge_index_id=knowledge_index_id,
                        generation=generation,
                        occurred_at_epoch=float(self._clock()),
                    )
                    db.add(event)
                    event_type = {
                        "activate": "index_activated",
                        "rollback": "index_rolled_back",
                        "reconcile": "index_reconciled",
                    }[action]
                    db.add(
                        SourceControlJobEventOutboxDB(
                            event_id=event.event_id,
                            tenant_id=tenant_id,
                            project_id=project_id,
                            resource_id=connection_id,
                            job_id=knowledge_index_id,
                            event_type=event_type,
                            status="completed",
                            reason_code=None,
                            trace_id=event.event_id,
                            occurred_at_epoch=event.occurred_at_epoch,
                            created_at_epoch=event.occurred_at_epoch,
                        )
                    )
                return self.get_active_index(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    owner_id=owner_id,
                    connection_id=connection_id,
                ) or self._missing_active()
        except IntegrityError:
            raise SourceControlPersistenceError(
                "source_control_generation_conflict"
            ) from None

    def get_active_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
    ) -> ActiveKnowledgeIndexRecord | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(ActiveKnowledgeIndexDB).where(
                    ActiveKnowledgeIndexDB.tenant_id == tenant_id,
                    ActiveKnowledgeIndexDB.project_id == project_id,
                    ActiveKnowledgeIndexDB.owner_id == owner_id,
                    ActiveKnowledgeIndexDB.connection_id == connection_id,
                )
            ).first()
            return None if row is None else self._active_record(row)

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
        with Session(self._engine) as db:
            index = self._require_scoped_index(
                db,
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=owner_id,
                knowledge_index_id=knowledge_index_id,
            )
            active = db.exec(
                select(ActiveKnowledgeIndexDB).where(
                    ActiveKnowledgeIndexDB.tenant_id == tenant_id,
                    ActiveKnowledgeIndexDB.project_id == project_id,
                    ActiveKnowledgeIndexDB.owner_id == owner_id,
                    ActiveKnowledgeIndexDB.connection_id == connection_id,
                )
            ).first()
            verified = db.exec(
                select(KnowledgeIndexRunSourceBindingDB).where(
                    KnowledgeIndexRunSourceBindingDB.knowledge_index_id
                    == knowledge_index_id,
                    KnowledgeIndexRunSourceBindingDB.status == "completed",
                    KnowledgeIndexRunSourceBindingDB.artifacts_verified
                    == True,  # noqa: E712
                )
            ).first()
            return derive_index_lifecycle(
                binding=self._index_record(index),
                active=None if active is None else self._active_record(active),
                current_source_revision_id=current_source_revision_id,
                current_policy_snapshot_digest=current_policy_snapshot_digest,
                has_verified_run=verified is not None,
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
        with Session(self._engine) as db:
            candidate = db.exec(
                select(KnowledgeIndexSourceBindingDB)
                .where(
                    KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
                    KnowledgeIndexSourceBindingDB.project_id == project_id,
                    KnowledgeIndexSourceBindingDB.owner_id == owner_id,
                    KnowledgeIndexSourceBindingDB.connection_id
                    == connection_id,
                    KnowledgeIndexSourceBindingDB.source_revision_id
                    == current_source_revision_id,
                    KnowledgeIndexSourceBindingDB.policy_snapshot_digest
                    == current_policy_snapshot_digest,
                    KnowledgeIndexSourceBindingDB.status == "completed",
                    KnowledgeIndexSourceBindingDB.activation_requested
                    == True,  # noqa: E712
                )
                .order_by(
                    KnowledgeIndexSourceBindingDB.updated_at_epoch.desc(),
                    KnowledgeIndexSourceBindingDB.knowledge_index_id.desc(),
                )
            ).first()
        active = self.get_active_index(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            connection_id=connection_id,
        )
        if candidate is None:
            return ActivationReconciliationResult(
                repaired=False,
                reason_code="no_pending_activation",
                active=active,
            )
        expected_generation = 0 if active is None else active.generation
        repaired = self.activate_index(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            connection_id=connection_id,
            knowledge_index_id=candidate.knowledge_index_id,
            current_source_revision_id=current_source_revision_id,
            current_policy_snapshot_digest=current_policy_snapshot_digest,
            expected_generation=expected_generation,
            action="reconcile",
        )
        return ActivationReconciliationResult(
            repaired=True,
            reason_code="pending_activation_repaired",
            active=repaired,
        )

    def list_activation_events(
        self, *, active_index_id: str
    ) -> tuple[ActiveKnowledgeIndexEventRecord, ...]:
        with Session(self._engine) as db:
            rows = db.exec(
                select(ActiveKnowledgeIndexEventDB)
                .where(
                    ActiveKnowledgeIndexEventDB.active_index_id
                    == active_index_id
                )
                .order_by(ActiveKnowledgeIndexEventDB.generation)
            ).all()
            return tuple(self._activation_event_record(row) for row in rows)

    @staticmethod
    def _require_digest(value: str) -> None:
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise SourceControlPersistenceError(
                "source_control_digest_invalid"
            )

    @staticmethod
    def _missing_active() -> ActiveKnowledgeIndexRecord:
        raise SourceControlPersistenceError(
            "source_control_activation_inconsistent"
        )

    @staticmethod
    def _require_scoped_revision(
        db: Session,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        source_revision_id: str,
    ) -> SourceRevisionDB:
        row = db.exec(
            select(SourceRevisionDB).where(
                SourceRevisionDB.source_revision_id == source_revision_id,
                SourceRevisionDB.tenant_id == tenant_id,
                SourceRevisionDB.project_id == project_id,
                SourceRevisionDB.owner_id == owner_id,
            )
        ).first()
        if row is None:
            raise SourceControlPersistenceError(
                "source_control_revision_not_found"
            )
        return row

    @staticmethod
    def _require_scoped_grant(
        db: Session,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        grant_id: str,
    ) -> SourceAccessGrantDB:
        row = db.exec(
            select(SourceAccessGrantDB).where(
                SourceAccessGrantDB.grant_id == grant_id,
                SourceAccessGrantDB.tenant_id == tenant_id,
                SourceAccessGrantDB.project_id == project_id,
                SourceAccessGrantDB.owner_id == owner_id,
            )
        ).first()
        if row is None:
            raise SourceControlPersistenceError(
                "source_control_grant_not_found"
            )
        return row

    @staticmethod
    def _require_scoped_index(
        db: Session,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        knowledge_index_id: str,
    ) -> KnowledgeIndexSourceBindingDB:
        row = db.exec(
            select(KnowledgeIndexSourceBindingDB).where(
                KnowledgeIndexSourceBindingDB.knowledge_index_id
                == knowledge_index_id,
                KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
                KnowledgeIndexSourceBindingDB.project_id == project_id,
                KnowledgeIndexSourceBindingDB.owner_id == owner_id,
            )
        ).first()
        if row is None:
            raise SourceControlPersistenceError(
                "source_control_index_binding_not_found"
            )
        return row

    def _add_grant_audit(
        self,
        db: Session,
        *,
        row: SourceAccessGrantDB,
        action: str,
        from_state: str | None,
        to_state: str | None,
        reason_code: str,
        grant_lock_version: int,
        occurred_at_epoch: float,
    ) -> None:
        audit_id = _stable_id(
            "audit",
            {
                "action": action,
                "grant_id": row.grant_id,
                "grant_lock_version": grant_lock_version,
                "reason_code": reason_code,
            },
        )
        if db.get(SourceAccessGrantAuditDB, audit_id) is not None:
            return
        db.add(
            SourceAccessGrantAuditDB(
                audit_id=audit_id,
                grant_id=row.grant_id,
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                owner_id=row.owner_id,
                action=action,
                from_state=from_state,
                to_state=to_state,
                reason_code=reason_code,
                grant_lock_version=grant_lock_version,
                occurred_at_epoch=occurred_at_epoch,
            )
        )

    @staticmethod
    def _grant_row(
        contract: SourceAccessGrant,
        *,
        owner_id: str,
        grant_family_id: str,
        rollback_of_grant_id: str | None,
        updated_at_epoch: float,
    ) -> SourceAccessGrantDB:
        return SourceAccessGrantDB(
            grant_id=contract.grant_id,
            grant_family_id=grant_family_id,
            grant_version=contract.version,
            tenant_id=contract.tenant_id,
            project_id=contract.project_id,
            owner_id=owner_id,
            source_revision_id=contract.source_revision_id,
            destination_id=contract.destination_id,
            operation=contract.operation.value,
            transformation=contract.transformation.value,
            purpose=contract.purpose,
            policy_version=contract.policy_version,
            state=contract.state.value,
            issued_at_epoch=contract.issued_at.timestamp(),
            expires_at_epoch=contract.expires_at.timestamp(),
            rollback_of_grant_id=rollback_of_grant_id,
            lock_version=1,
            updated_at_epoch=updated_at_epoch,
        )

    @staticmethod
    def _new_connection_row(
        contract: SourceConnection, *, now: float
    ) -> SourceConnectionDB:
        return SourceConnectionDB(
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

    @staticmethod
    def _selector_binding(
        row: SourceConnectionSelectorDB,
    ) -> SourceConnectionSelectorBinding:
        binding = SourceConnectionSelectorBinding(
            connection_id=row.connection_id,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            owner_id=row.owner_id,
            public_connector_type=row.public_connector_type,
            implementation_connector_type=(
                row.implementation_connector_type
            ),
            selector_kind=row.selector_kind,
            selector_id=row.selector_id,
            relative_path=row.relative_path,
            repository_identifier=row.repository_identifier,
        )
        if binding.binding_digest != row.binding_digest:
            raise SourceControlPersistenceError(
                "source_control_connection_selector_digest_mismatch"
            )
        return binding

    @staticmethod
    def _connection_record(row: SourceConnectionDB) -> SourceConnectionRecord:
        return SourceConnectionRecord(
            contract=SourceConnection(
                schema="ananta.source-control.source-connection.v1",
                authority="hub",
                connection_id=row.connection_id,
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                owner_id=row.owner_id,
                connector_type=row.connector_type,
                connection_identity_digest=row.connection_identity_digest,
                display_name=row.display_name,
                sensitivity=row.sensitivity,
                state=row.state,
                created_at=_datetime(row.created_at_epoch),
            ),
            lock_version=row.lock_version,
            updated_at_epoch=row.updated_at_epoch,
            disabled_at_epoch=row.disabled_at_epoch,
            tombstoned_at_epoch=row.tombstoned_at_epoch,
        )

    @staticmethod
    def _revision_record(row: SourceRevisionDB) -> SourceRevisionRecord:
        return SourceRevisionRecord(
            contract=SourceRevision(
                schema="ananta.source-control.source-revision.v1",
                authority="hub",
                source_revision_id=row.source_revision_id,
                connection_id=row.connection_id,
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                owner_id=row.owner_id,
                connector_type=row.connector_type,
                sensitivity=row.sensitivity,
                revision_token=row.revision_token,
                revision_digest=row.revision_digest,
                content_manifest_id=row.content_manifest_id,
                content_manifest_digest=row.content_manifest_digest,
                admission_state=row.admission_state,
                captured_at=_datetime(row.captured_at_epoch),
            )
        )

    @staticmethod
    def _grant_record(row: SourceAccessGrantDB) -> SourceAccessGrantRecord:
        return SourceAccessGrantRecord(
            contract=SourceAccessGrant(
                schema="ananta.source-control.source-access-grant.v1",
                authority="hub",
                grant_id=row.grant_id,
                version=row.grant_version,
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                source_revision_id=row.source_revision_id,
                destination_id=row.destination_id,
                operation=row.operation,
                transformation=row.transformation,
                purpose=row.purpose,
                policy_version=row.policy_version,
                state=row.state,
                issued_at=_datetime(row.issued_at_epoch),
                expires_at=_datetime(row.expires_at_epoch),
            ),
            owner_id=row.owner_id,
            grant_family_id=row.grant_family_id,
            lock_version=row.lock_version,
            updated_at_epoch=row.updated_at_epoch,
            rollback_of_grant_id=row.rollback_of_grant_id,
        )

    @staticmethod
    def _grant_audit_record(
        row: SourceAccessGrantAuditDB,
    ) -> SourceAccessGrantAuditRecord:
        return SourceAccessGrantAuditRecord(
            audit_id=row.audit_id,
            grant_id=row.grant_id,
            action=row.action,
            from_state=row.from_state,
            to_state=row.to_state,
            reason_code=row.reason_code,
            grant_lock_version=row.grant_lock_version,
            occurred_at_epoch=row.occurred_at_epoch,
        )

    @staticmethod
    def _index_record(
        row: KnowledgeIndexSourceBindingDB,
    ) -> KnowledgeIndexBindingRecord:
        return KnowledgeIndexBindingRecord(
            knowledge_index_id=row.knowledge_index_id,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            owner_id=row.owner_id,
            connection_id=row.connection_id,
            source_revision_id=row.source_revision_id,
            policy_snapshot_id=row.policy_snapshot_id,
            policy_snapshot_digest=row.policy_snapshot_digest,
            index_contract_version=row.index_contract_version,
            status=row.status,
            artifact_manifest_digest=row.artifact_manifest_digest,
            activation_requested=row.activation_requested,
            lock_version=row.lock_version,
            created_at_epoch=row.created_at_epoch,
            updated_at_epoch=row.updated_at_epoch,
        )

    @staticmethod
    def _run_record(
        row: KnowledgeIndexRunSourceBindingDB,
    ) -> KnowledgeIndexRunBindingRecord:
        return KnowledgeIndexRunBindingRecord(
            index_run_id=row.index_run_id,
            knowledge_index_id=row.knowledge_index_id,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            owner_id=row.owner_id,
            source_revision_id=row.source_revision_id,
            policy_snapshot_id=row.policy_snapshot_id,
            policy_snapshot_digest=row.policy_snapshot_digest,
            status=row.status,
            artifact_manifest_digest=row.artifact_manifest_digest,
            artifacts_verified=row.artifacts_verified,
            lock_version=row.lock_version,
            created_at_epoch=row.created_at_epoch,
            completed_at_epoch=row.completed_at_epoch,
        )

    @staticmethod
    def _active_record(
        row: ActiveKnowledgeIndexDB,
    ) -> ActiveKnowledgeIndexRecord:
        return ActiveKnowledgeIndexRecord(
            active_index_id=row.active_index_id,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            owner_id=row.owner_id,
            connection_id=row.connection_id,
            source_revision_id=row.source_revision_id,
            policy_snapshot_digest=row.policy_snapshot_digest,
            knowledge_index_id=row.knowledge_index_id,
            previous_knowledge_index_id=row.previous_knowledge_index_id,
            generation=row.generation,
            updated_at_epoch=row.updated_at_epoch,
        )

    @staticmethod
    def _activation_event_record(
        row: ActiveKnowledgeIndexEventDB,
    ) -> ActiveKnowledgeIndexEventRecord:
        return ActiveKnowledgeIndexEventRecord(
            event_id=row.event_id,
            active_index_id=row.active_index_id,
            action=row.action,
            from_knowledge_index_id=row.from_knowledge_index_id,
            to_knowledge_index_id=row.to_knowledge_index_id,
            generation=row.generation,
            occurred_at_epoch=row.occurred_at_epoch,
        )
