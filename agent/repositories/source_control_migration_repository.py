"""Transactional SQLModel adapter for legacy source-control adoption."""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy import delete, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceConnectionDB,
    SourceRevisionDB,
)
from agent.db_models.source_control_migration import (
    SourceControlLegacyMappingDB,
    SourceControlMigrationRunDB,
    SourceRefMappingDB,
)
from agent.services.source_control_legacy_migration import (
    LegacyMappingRecord,
    LegacyMigrationEntry,
    LegacyMigrationPlan,
    MigrationRunRecord,
    SourceControlMigrationError,
)
from ananta_contracts.source_control import SourceConnection, SourceRevision


class SQLSourceControlMigrationRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], float] = time.time,
        apply_fault_hook: Callable[[LegacyMigrationEntry], None] | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._apply_fault_hook = apply_fault_hook

    def begin(
        self,
        plan: LegacyMigrationPlan,
        *,
        resume: bool,
    ) -> MigrationRunRecord:
        with Session(self._engine) as db:
            with db.begin():
                row = db.get(SourceControlMigrationRunDB, plan.migration_id)
                if row is None:
                    row = SourceControlMigrationRunDB(
                        migration_id=plan.migration_id,
                        tenant_id=plan.tenant_id,
                        project_id=plan.project_id,
                        owner_id=plan.owner_id,
                        inventory_digest=plan.inventory_digest,
                        state="applying",
                        cursor=0,
                        total_entries=len(plan.entries),
                        created_mapping_count=0,
                        reused_mapping_count=0,
                        conflict_count=0,
                        lock_version=1,
                        started_at_epoch=float(self._clock()),
                        updated_at_epoch=float(self._clock()),
                    )
                    db.add(row)
                    db.flush()
                else:
                    if (
                        row.inventory_digest != plan.inventory_digest
                        or row.tenant_id != plan.tenant_id
                        or row.project_id != plan.project_id
                        or row.owner_id != plan.owner_id
                        or row.total_entries != len(plan.entries)
                    ):
                        raise SourceControlMigrationError(
                            "source_control_migration_identity_conflict"
                        )
                    if row.state == "aborted":
                        if not resume:
                            raise SourceControlMigrationError(
                                "source_control_migration_resume_required"
                            )
                        row.state = "applying"
                        row.failure_reason = None
                        row.lock_version += 1
                        row.updated_at_epoch = float(self._clock())
                    elif row.state == "rolled_back":
                        raise SourceControlMigrationError(
                            "source_control_migration_already_rolled_back"
                        )
            return self._run_record(row)

    def apply_entry(
        self,
        *,
        migration_id: str,
        expected_cursor: int,
        entry: LegacyMigrationEntry,
    ) -> MigrationRunRecord:
        try:
            with Session(self._engine) as db:
                with db.begin():
                    run = db.get(SourceControlMigrationRunDB, migration_id)
                    if run is None:
                        raise SourceControlMigrationError(
                            "source_control_migration_not_found"
                        )
                    if run.state != "applying":
                        raise SourceControlMigrationError(
                            "source_control_migration_not_applying"
                        )
                    if (
                        run.cursor != expected_cursor
                        or entry.sequence != expected_cursor + 1
                    ):
                        raise SourceControlMigrationError(
                            "source_control_migration_cursor_conflict"
                        )
                    existing_mapping = db.get(
                        SourceControlLegacyMappingDB,
                        entry.mapping_id,
                    )
                    if existing_mapping is not None:
                        if (
                            existing_mapping.migration_id != migration_id
                            or existing_mapping.sequence != entry.sequence
                            or existing_mapping.legacy_record_digest
                            != entry.legacy_record_digest
                        ):
                            raise SourceControlMigrationError(
                                "source_control_legacy_mapping_conflict"
                            )
                        created = False
                        flags = (False, False, False)
                    else:
                        prior_mapping = db.exec(
                            select(SourceControlLegacyMappingDB)
                            .where(
                                SourceControlLegacyMappingDB.tenant_id
                                == entry.tenant_id,
                                SourceControlLegacyMappingDB.project_id
                                == entry.project_id,
                                SourceControlLegacyMappingDB.owner_id
                                == entry.owner_id,
                                SourceControlLegacyMappingDB.legacy_kind
                                == entry.legacy_kind,
                                SourceControlLegacyMappingDB.legacy_key
                                == entry.legacy_key,
                                SourceControlLegacyMappingDB.legacy_record_digest
                                == entry.legacy_record_digest,
                            )
                            .order_by(
                                SourceControlLegacyMappingDB.created_at_epoch,
                                SourceControlLegacyMappingDB.mapping_id,
                            )
                        ).first()
                        self._ensure_connection(db, entry)
                        self._ensure_revision(db, entry)
                        created_source_ref = self._ensure_source_ref(
                            db, entry
                        )
                        created_index = self._ensure_index_binding(db, entry)
                        created_run = self._ensure_run_binding(db, entry)
                        flags = (
                            created_source_ref,
                            created_index,
                            created_run,
                        )
                        created = prior_mapping is None or any(flags)
                        if self._apply_fault_hook is not None:
                            self._apply_fault_hook(entry)
                        db.add(
                            SourceControlLegacyMappingDB(
                                mapping_id=entry.mapping_id,
                                migration_id=migration_id,
                                sequence=entry.sequence,
                                tenant_id=entry.tenant_id,
                                project_id=entry.project_id,
                                owner_id=entry.owner_id,
                                legacy_kind=entry.legacy_kind,
                                legacy_key=entry.legacy_key,
                                legacy_record_digest=(
                                    entry.legacy_record_digest
                                ),
                                connection_id=(
                                    entry.connection.connection_id
                                    if entry.connection
                                    else None
                                ),
                                source_revision_id=(
                                    entry.revision.source_revision_id
                                    if entry.revision
                                    else (
                                        entry.index_binding.source_revision_id
                                        if entry.index_binding
                                        else (
                                            entry.run_binding.source_revision_id
                                            if entry.run_binding
                                            else None
                                        )
                                    )
                                ),
                                source_ref_id=(
                                    entry.source_ref.source_ref_id
                                    if entry.source_ref
                                    else None
                                ),
                                knowledge_index_id=(
                                    entry.index_binding.knowledge_index_id
                                    if entry.index_binding
                                    else (
                                        entry.run_binding.knowledge_index_id
                                        if entry.run_binding
                                        else None
                                    )
                                ),
                                index_run_id=(
                                    entry.run_binding.index_run_id
                                    if entry.run_binding
                                    else None
                                ),
                                policy_snapshot_id=entry.policy_snapshot_id,
                                policy_version=entry.policy_version,
                                created_source_ref_mapping=flags[0],
                                created_index_binding=flags[1],
                                created_run_binding=flags[2],
                                created_at_epoch=float(self._clock()),
                            )
                        )
                    result = db.execute(
                        update(SourceControlMigrationRunDB)
                        .where(
                            SourceControlMigrationRunDB.migration_id
                            == migration_id,
                            SourceControlMigrationRunDB.cursor
                            == expected_cursor,
                            SourceControlMigrationRunDB.lock_version
                            == run.lock_version,
                        )
                        .values(
                            cursor=entry.sequence,
                            created_mapping_count=(
                                run.created_mapping_count
                                + (1 if created else 0)
                            ),
                            reused_mapping_count=(
                                run.reused_mapping_count
                                + (0 if created else 1)
                            ),
                            lock_version=run.lock_version + 1,
                            updated_at_epoch=float(self._clock()),
                        )
                    )
                    if result.rowcount != 1:
                        raise SourceControlMigrationError(
                            "source_control_migration_cursor_conflict"
                        )
                refreshed = db.get(
                    SourceControlMigrationRunDB,
                    migration_id,
                )
                if refreshed is None:
                    raise SourceControlMigrationError(
                        "source_control_migration_not_found"
                    )
                return self._run_record(refreshed)
        except IntegrityError:
            raise SourceControlMigrationError(
                "source_control_legacy_mapping_conflict"
            ) from None

    def finish(
        self,
        *,
        migration_id: str,
        expected_cursor: int,
    ) -> MigrationRunRecord:
        with Session(self._engine) as db:
            with db.begin():
                row = db.get(SourceControlMigrationRunDB, migration_id)
                if (
                    row is None
                    or row.state != "applying"
                    or row.cursor != expected_cursor
                    or row.cursor != row.total_entries
                ):
                    raise SourceControlMigrationError(
                        "source_control_migration_finish_invalid"
                    )
                row.state = "applied"
                row.lock_version += 1
                row.updated_at_epoch = float(self._clock())
                row.completed_at_epoch = float(self._clock())
            return self._run_record(row)

    def abort(
        self,
        *,
        migration_id: str,
        expected_cursor: int,
        reason_code: str,
    ) -> MigrationRunRecord:
        with Session(self._engine) as db:
            with db.begin():
                row = db.get(SourceControlMigrationRunDB, migration_id)
                if (
                    row is None
                    or row.state != "applying"
                    or row.cursor != expected_cursor
                ):
                    raise SourceControlMigrationError(
                        "source_control_migration_abort_invalid"
                    )
                row.state = "aborted"
                row.failure_reason = reason_code[:160]
                row.conflict_count += 1
                row.lock_version += 1
                row.updated_at_epoch = float(self._clock())
            return self._run_record(row)

    def get_run(self, migration_id: str) -> MigrationRunRecord | None:
        with Session(self._engine) as db:
            row = db.get(SourceControlMigrationRunDB, migration_id)
            return None if row is None else self._run_record(row)

    def list_mappings(
        self, migration_id: str
    ) -> tuple[LegacyMappingRecord, ...]:
        with Session(self._engine) as db:
            rows = db.exec(
                select(SourceControlLegacyMappingDB)
                .where(
                    SourceControlLegacyMappingDB.migration_id
                    == migration_id
                )
                .order_by(SourceControlLegacyMappingDB.sequence)
            ).all()
            return tuple(self._mapping_record(row) for row in rows)

    def rollback_new_mappings(
        self, migration_id: str
    ) -> MigrationRunRecord:
        with Session(self._engine) as db:
            with db.begin():
                run = db.get(SourceControlMigrationRunDB, migration_id)
                if run is None:
                    raise SourceControlMigrationError(
                        "source_control_migration_not_found"
                    )
                if run.state not in {"applied", "aborted"}:
                    raise SourceControlMigrationError(
                        "source_control_migration_rollback_invalid"
                    )
                mappings = db.exec(
                    select(SourceControlLegacyMappingDB)
                    .where(
                        SourceControlLegacyMappingDB.migration_id
                        == migration_id
                    )
                    .order_by(SourceControlLegacyMappingDB.sequence.desc())
                ).all()
                for mapping in mappings:
                    if mapping.created_run_binding and mapping.index_run_id:
                        db.execute(
                            delete(KnowledgeIndexRunSourceBindingDB).where(
                                KnowledgeIndexRunSourceBindingDB.index_run_id
                                == mapping.index_run_id
                            )
                        )
                    if (
                        mapping.created_index_binding
                        and mapping.knowledge_index_id
                    ):
                        active = db.exec(
                            select(ActiveKnowledgeIndexDB).where(
                                ActiveKnowledgeIndexDB.knowledge_index_id
                                == mapping.knowledge_index_id
                            )
                        ).first()
                        if active is not None:
                            raise SourceControlMigrationError(
                                "source_control_migration_mapping_in_use"
                            )
                        db.execute(
                            delete(KnowledgeIndexSourceBindingDB).where(
                                KnowledgeIndexSourceBindingDB.knowledge_index_id
                                == mapping.knowledge_index_id
                            )
                        )
                    if (
                        mapping.created_source_ref_mapping
                        and mapping.source_ref_id
                    ):
                        db.execute(
                            delete(SourceRefMappingDB).where(
                                SourceRefMappingDB.source_ref_id
                                == mapping.source_ref_id
                            )
                        )
                    db.delete(mapping)
                run.state = "rolled_back"
                run.lock_version += 1
                run.updated_at_epoch = float(self._clock())
                run.completed_at_epoch = float(self._clock())
            return self._run_record(run)

    @staticmethod
    def _ensure_connection(
        db: Session,
        entry: LegacyMigrationEntry,
    ) -> None:
        contract = entry.connection
        if contract is None:
            return
        row = db.get(SourceConnectionDB, contract.connection_id)
        if row is None:
            db.add(
                SourceConnectionDB(
                    connection_id=contract.connection_id,
                    tenant_id=contract.tenant_id,
                    project_id=contract.project_id,
                    owner_id=contract.owner_id,
                    connector_type=contract.connector_type.value,
                    connection_identity_digest=(
                        contract.connection_identity_digest
                    ),
                    display_name=contract.display_name,
                    sensitivity=contract.sensitivity.value,
                    state=contract.state.value,
                    lock_version=1,
                    created_at_epoch=contract.created_at.timestamp(),
                    updated_at_epoch=contract.created_at.timestamp(),
                )
            )
            db.flush()
            return
        if SQLSourceControlMigrationRepository._connection_contract(row) != contract:
            raise SourceControlMigrationError(
                "source_control_connection_identity_conflict"
            )

    @staticmethod
    def _ensure_revision(
        db: Session,
        entry: LegacyMigrationEntry,
    ) -> None:
        contract = entry.revision
        if contract is None:
            return
        row = db.get(SourceRevisionDB, contract.source_revision_id)
        if row is None:
            db.add(
                SourceRevisionDB(
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
                    content_manifest_digest=(
                        contract.content_manifest_digest
                    ),
                    admission_state=contract.admission_state.value,
                    captured_at_epoch=contract.captured_at.timestamp(),
                )
            )
            db.flush()
            return
        if SQLSourceControlMigrationRepository._revision_contract(row) != contract:
            raise SourceControlMigrationError(
                "source_control_revision_append_conflict"
            )

    def _ensure_source_ref(
        self,
        db: Session,
        entry: LegacyMigrationEntry,
    ) -> bool:
        contract = entry.source_ref
        if contract is None:
            return False
        row = db.get(SourceRefMappingDB, contract.source_ref_id)
        if row is None:
            db.add(
                SourceRefMappingDB(
                    source_ref_id=contract.source_ref_id,
                    connection_id=contract.connection_id,
                    source_revision_id=contract.source_revision_id,
                    tenant_id=contract.tenant_id,
                    project_id=contract.project_id,
                    owner_id=entry.owner_id,
                    provenance_digest=contract.provenance_digest,
                    created_at_epoch=float(self._clock()),
                )
            )
            db.flush()
            return True
        if (
            row.connection_id != contract.connection_id
            or row.source_revision_id != contract.source_revision_id
            or row.tenant_id != contract.tenant_id
            or row.project_id != contract.project_id
            or row.owner_id != entry.owner_id
            or row.provenance_digest != contract.provenance_digest
        ):
            raise SourceControlMigrationError(
                "source_control_source_ref_mapping_conflict"
            )
        return False

    @staticmethod
    def _ensure_index_binding(
        db: Session,
        entry: LegacyMigrationEntry,
    ) -> bool:
        record = entry.index_binding
        if record is None:
            return False
        row = db.get(
            KnowledgeIndexSourceBindingDB,
            record.knowledge_index_id,
        )
        if row is None:
            db.add(KnowledgeIndexSourceBindingDB(**record.__dict__))
            db.flush()
            return True
        if SQLSourceControlMigrationRepository._index_values(row) != record:
            raise SourceControlMigrationError(
                "source_control_index_binding_conflict"
            )
        return False

    @staticmethod
    def _ensure_run_binding(
        db: Session,
        entry: LegacyMigrationEntry,
    ) -> bool:
        record = entry.run_binding
        if record is None:
            return False
        row = db.get(
            KnowledgeIndexRunSourceBindingDB,
            record.index_run_id,
        )
        if row is None:
            db.add(KnowledgeIndexRunSourceBindingDB(**record.__dict__))
            db.flush()
            return True
        if SQLSourceControlMigrationRepository._run_values(row) != record:
            raise SourceControlMigrationError(
                "source_control_index_run_binding_conflict"
            )
        return False

    @staticmethod
    def _connection_contract(row: SourceConnectionDB) -> SourceConnection:
        from datetime import datetime, timezone

        return SourceConnection(
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
            created_at=datetime.fromtimestamp(
                row.created_at_epoch,
                tz=timezone.utc,
            ),
        )

    @staticmethod
    def _revision_contract(row: SourceRevisionDB) -> SourceRevision:
        from datetime import datetime, timezone

        return SourceRevision(
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
            captured_at=datetime.fromtimestamp(
                row.captured_at_epoch,
                tz=timezone.utc,
            ),
        )

    @staticmethod
    def _index_values(
        row: KnowledgeIndexSourceBindingDB,
    ):
        from agent.services.source_control_persistence import (
            KnowledgeIndexBindingRecord,
        )

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
    def _run_values(
        row: KnowledgeIndexRunSourceBindingDB,
    ):
        from agent.services.source_control_persistence import (
            KnowledgeIndexRunBindingRecord,
        )

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
    def _run_record(row: SourceControlMigrationRunDB) -> MigrationRunRecord:
        return MigrationRunRecord(
            migration_id=row.migration_id,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            owner_id=row.owner_id,
            inventory_digest=row.inventory_digest,
            state=row.state,
            cursor=row.cursor,
            total_entries=row.total_entries,
            created_mapping_count=row.created_mapping_count,
            reused_mapping_count=row.reused_mapping_count,
            conflict_count=row.conflict_count,
            lock_version=row.lock_version,
            failure_reason=row.failure_reason,
            started_at_epoch=row.started_at_epoch,
            updated_at_epoch=row.updated_at_epoch,
            completed_at_epoch=row.completed_at_epoch,
        )

    @staticmethod
    def _mapping_record(
        row: SourceControlLegacyMappingDB,
    ) -> LegacyMappingRecord:
        return LegacyMappingRecord(
            mapping_id=row.mapping_id,
            migration_id=row.migration_id,
            sequence=row.sequence,
            legacy_kind=row.legacy_kind,
            legacy_key=row.legacy_key,
            legacy_record_digest=row.legacy_record_digest,
            connection_id=row.connection_id,
            source_revision_id=row.source_revision_id,
            source_ref_id=row.source_ref_id,
            knowledge_index_id=row.knowledge_index_id,
            index_run_id=row.index_run_id,
            policy_snapshot_id=row.policy_snapshot_id,
            policy_version=row.policy_version,
            created_source_ref_mapping=row.created_source_ref_mapping,
            created_index_binding=row.created_index_binding,
            created_run_binding=row.created_run_binding,
        )
