"""SQL repository behind the Source index lifecycle service (history, activation, purge).
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from sqlalchemy import delete, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent.db_models.knowledge_index_execution import (
    KnowledgeIndexExecutionBindingDB,
)
from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantDB,
    SourceControlIndexReferenceDB,
    SourceConnectionDB,
    SourceRevisionDB,
)
from agent.repositories.source_control_repository import (
    SQLSourceControlRepository,
)
from agent.services.source_control_production_adapters import (
    ContainedArtifactDeletionService,
)
from agent.services.source_control_runtime_support import (
    decode_cursor,
    encode_cursor,
    iso_timestamp,
)
from agent.services.source_control_runtime_support import SENSITIVE_SENSITIVITIES
from agent.services.source_index_lifecycle_service import (
    ActiveIndexPointer,
    PurgeBlocker,
    SourceIndexHistoryPage,
    SourceIndexLifecycleError,
    SourceIndexRecord,
)


class SQLSourceIndexLifecycleRepository:
    """CAS lifecycle adapter over canonical source-control persistence."""

    def __init__(
        self,
        engine: Engine,
        *,
        artifact_deletion: ContainedArtifactDeletionService | None = None,
        clock=time.time,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._canonical = SQLSourceControlRepository(engine, clock=clock)
        self._artifact_deletion = artifact_deletion

    def list_history(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        cursor: str | None,
        limit: int,
    ) -> SourceIndexHistoryPage:
        after_id = decode_cursor(cursor)
        with Session(self._engine) as db:
            statement = select(KnowledgeIndexSourceBindingDB).where(
                KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
                KnowledgeIndexSourceBindingDB.project_id == project_id,
                KnowledgeIndexSourceBindingDB.connection_id == connection_id,
            )
            if after_id is not None:
                statement = statement.where(
                    KnowledgeIndexSourceBindingDB.knowledge_index_id > after_id
                )
            rows = list(
                db.exec(
                    statement.order_by(
                        KnowledgeIndexSourceBindingDB.knowledge_index_id
                    ).limit(limit + 1)
                ).all()
            )
            selected = rows[:limit]
            return SourceIndexHistoryPage(
                items=tuple(self._index(db, row) for row in selected),
                active=self._active(
                    db, tenant_id, project_id, connection_id
                ),
                next_cursor=(
                    encode_cursor(selected[-1].knowledge_index_id)
                    if len(rows) > limit and selected
                    else None
                ),
            )

    def get_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        knowledge_index_id: str,
    ) -> SourceIndexRecord | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(KnowledgeIndexSourceBindingDB).where(
                    KnowledgeIndexSourceBindingDB.knowledge_index_id
                    == knowledge_index_id,
                    KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
                    KnowledgeIndexSourceBindingDB.project_id == project_id,
                )
            ).first()
            return None if row is None else self._index(db, row)

    def get_active(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
    ) -> ActiveIndexPointer | None:
        with Session(self._engine) as db:
            return self._active(db, tenant_id, project_id, connection_id)

    def compare_and_activate(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        knowledge_index_id: str,
        source_revision_id: str,
        expected_generation: int,
        actor_id: str,
        reason_code: str,
    ) -> ActiveIndexPointer:
        del actor_id
        with Session(self._engine) as db:
            row = db.exec(
                select(KnowledgeIndexSourceBindingDB).where(
                    KnowledgeIndexSourceBindingDB.knowledge_index_id
                    == knowledge_index_id,
                    KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
                    KnowledgeIndexSourceBindingDB.project_id == project_id,
                )
            ).first()
            if row is None:
                raise SourceIndexLifecycleError("knowledge_index_not_found")
            owner_id = row.owner_id
            policy_digest = row.policy_snapshot_digest
        self._canonical.activate_index(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            connection_id=connection_id,
            knowledge_index_id=knowledge_index_id,
            current_source_revision_id=source_revision_id,
            current_policy_snapshot_digest=policy_digest,
            expected_generation=expected_generation,
            action=(
                "rollback"
                if reason_code == "index_rolled_back"
                else "activate"
            ),
        )
        pointer = self.get_active(
            tenant_id=tenant_id,
            project_id=project_id,
            connection_id=connection_id,
        )
        if pointer is None:
            raise SourceIndexLifecycleError("active_index_write_failed")
        return pointer

    def disable_connection(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        expected_version: int,
        actor_id: str,
    ) -> int:
        del actor_id
        with Session(self._engine) as db:
            mutation = db.exec(
                update(SourceConnectionDB)
                .where(
                    SourceConnectionDB.connection_id == connection_id,
                    SourceConnectionDB.tenant_id == tenant_id,
                    SourceConnectionDB.project_id == project_id,
                    SourceConnectionDB.lock_version == expected_version,
                    SourceConnectionDB.state != "tombstoned",
                )
                .values(
                    state="disabled",
                    lock_version=expected_version + 1,
                    updated_at_epoch=float(self._clock()),
                )
            )
            if mutation.rowcount != 1:
                db.rollback()
                raise SourceIndexLifecycleError(
                    self._connection_failure(
                        db, tenant_id, project_id, connection_id
                    )
                )
            db.commit()
        return expected_version + 1

    def tombstone_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        knowledge_index_id: str,
        actor_id: str,
        expected_version: int,
    ) -> int:
        del actor_id
        with Session(self._engine) as db:
            mutation = db.exec(
                update(KnowledgeIndexSourceBindingDB)
                .where(
                    KnowledgeIndexSourceBindingDB.knowledge_index_id
                    == knowledge_index_id,
                    KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
                    KnowledgeIndexSourceBindingDB.project_id == project_id,
                    KnowledgeIndexSourceBindingDB.lock_version
                    == expected_version,
                )
                .values(
                    status="tombstoned",
                    lock_version=expected_version + 1,
                    updated_at_epoch=float(self._clock()),
                )
            )
            if mutation.rowcount != 1:
                db.rollback()
                raise SourceIndexLifecycleError(
                    self._index_failure(
                        db, tenant_id, project_id, knowledge_index_id
                    )
                )
            db.commit()
        return expected_version + 1

    def purge_blockers(
        self,
        *,
        tenant_id: str,
        project_id: str,
        knowledge_index_id: str,
    ) -> Sequence[PurgeBlocker]:
        now = float(self._clock())
        with Session(self._engine) as db:
            index = db.exec(
                select(KnowledgeIndexSourceBindingDB).where(
                    KnowledgeIndexSourceBindingDB.knowledge_index_id
                    == knowledge_index_id,
                    KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
                    KnowledgeIndexSourceBindingDB.project_id == project_id,
                )
            ).first()
            if index is None:
                raise SourceIndexLifecycleError(
                    "knowledge_index_not_found"
                )
            runs = list(
                db.exec(
                    select(KnowledgeIndexRunSourceBindingDB).where(
                        KnowledgeIndexRunSourceBindingDB.knowledge_index_id
                        == knowledge_index_id,
                        KnowledgeIndexRunSourceBindingDB.tenant_id == tenant_id,
                        KnowledgeIndexRunSourceBindingDB.project_id == project_id,
                    )
                ).all()
            )
            grants = list(
                db.exec(
                    select(SourceAccessGrantDB).where(
                        SourceAccessGrantDB.tenant_id == tenant_id,
                        SourceAccessGrantDB.project_id == project_id,
                        SourceAccessGrantDB.source_revision_id
                        == index.source_revision_id,
                        SourceAccessGrantDB.state == "active",
                        SourceAccessGrantDB.expires_at_epoch > now,
                    )
                ).all()
            )
            leases = list(
                db.exec(
                    select(KnowledgeIndexExecutionBindingDB).where(
                        KnowledgeIndexExecutionBindingDB.tenant_id
                        == tenant_id,
                        KnowledgeIndexExecutionBindingDB.project_id
                        == project_id,
                        KnowledgeIndexExecutionBindingDB.source_revision_id
                        == index.source_revision_id,
                        KnowledgeIndexExecutionBindingDB.lease_expires_epoch_ms
                        > int(now * 1000),
                        KnowledgeIndexExecutionBindingDB.state.notin_(
                            ("completed", "failed", "cancelled", "expired")
                        ),
                    )
                ).all()
            )
            references = list(
                db.exec(
                    select(SourceControlIndexReferenceDB).where(
                        SourceControlIndexReferenceDB.tenant_id == tenant_id,
                        SourceControlIndexReferenceDB.project_id == project_id,
                        SourceControlIndexReferenceDB.knowledge_index_id
                        == knowledge_index_id,
                        SourceControlIndexReferenceDB.state == "active",
                    )
                ).all()
            )
        blockers: list[PurgeBlocker] = [
            PurgeBlocker("active_grant", row.grant_id) for row in grants
        ]
        blockers.extend(
            PurgeBlocker("active_lease", row.lease_id) for row in leases
        )
        blockers.extend(
            PurgeBlocker(row.reference_kind, row.reference_id)
            for row in references
            if row.expires_at_epoch is None
            or float(row.expires_at_epoch) > now
        )
        artifacts_deleted = bool(
            self._artifact_deletion is not None
            and self._artifact_deletion.is_deleted(
                knowledge_index_id=knowledge_index_id
            )
        )
        if self._artifact_deletion is None and not artifacts_deleted:
            blockers.extend(
                PurgeBlocker("artifact_ref", row.index_run_id)
                for row in runs
                if row.artifact_manifest_digest
            )
        return tuple(blockers)

    def purge_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        knowledge_index_id: str,
        actor_id: str,
        expected_version: int,
        approval_id: str | None,
    ) -> None:
        del actor_id, approval_id
        with Session(self._engine) as db:
            row = db.exec(
                select(KnowledgeIndexSourceBindingDB).where(
                    KnowledgeIndexSourceBindingDB.knowledge_index_id
                    == knowledge_index_id,
                    KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
                    KnowledgeIndexSourceBindingDB.project_id == project_id,
                )
            ).first()
            if row is None:
                raise SourceIndexLifecycleError("knowledge_index_not_found")
            if int(row.lock_version) != expected_version:
                raise SourceIndexLifecycleError("index_version_conflict")
            db.exec(
                delete(KnowledgeIndexRunSourceBindingDB).where(
                    KnowledgeIndexRunSourceBindingDB.knowledge_index_id
                    == knowledge_index_id,
                    KnowledgeIndexRunSourceBindingDB.tenant_id == tenant_id,
                    KnowledgeIndexRunSourceBindingDB.project_id == project_id,
                )
            )
            db.delete(row)
            db.commit()

    @staticmethod
    def _active(
        db: Session,
        tenant_id: str,
        project_id: str,
        connection_id: str,
    ) -> ActiveIndexPointer | None:
        row = db.exec(
            select(ActiveKnowledgeIndexDB).where(
                ActiveKnowledgeIndexDB.tenant_id == tenant_id,
                ActiveKnowledgeIndexDB.project_id == project_id,
                ActiveKnowledgeIndexDB.connection_id == connection_id,
            )
        ).first()
        if row is None:
            return None
        return ActiveIndexPointer(
            connection_id=row.connection_id,
            knowledge_index_id=row.knowledge_index_id,
            source_revision_id=row.source_revision_id,
            generation=int(row.generation),
        )

    @staticmethod
    def _index(
        db: Session, row: KnowledgeIndexSourceBindingDB
    ) -> SourceIndexRecord:
        runs = list(
            db.exec(
                select(KnowledgeIndexRunSourceBindingDB).where(
                    KnowledgeIndexRunSourceBindingDB.knowledge_index_id
                    == row.knowledge_index_id
                )
            ).all()
        )
        run = max(
            runs, key=lambda item: float(item.created_at_epoch), default=None
        )
        revision = db.get(SourceRevisionDB, row.source_revision_id)
        if revision is None:
            raise SourceIndexLifecycleError(
                "source_revision_projection_missing"
            )
        manifest_digest = (
            (run.artifact_manifest_digest if run is not None else None)
            or row.artifact_manifest_digest
        )
        if not manifest_digest:
            raise SourceIndexLifecycleError(
                "artifact_manifest_projection_missing"
            )
        return SourceIndexRecord(
            knowledge_index_id=row.knowledge_index_id,
            run_id=(
                run.index_run_id
                if run is not None
                else row.knowledge_index_id
            ),
            connection_id=row.connection_id,
            source_revision_id=row.source_revision_id,
            revision_digest=revision.revision_digest,
            policy_digest=row.policy_snapshot_digest,
            manifest_digest=manifest_digest,
            status=row.status,
            coverage={},
            artifact_verified=bool(
                run.artifacts_verified if run is not None else False
            ),
            completed_at=(
                iso_timestamp(run.completed_at_epoch) if run is not None else None
            ),
            tombstoned=row.status == "tombstoned",
            sensitive=bool(
                revision is not None and revision.sensitivity in SENSITIVE_SENSITIVITIES
            ),
        )

    @staticmethod
    def _connection_failure(
        db: Session, tenant_id: str, project_id: str, connection_id: str
    ) -> str:
        row = db.exec(
            select(SourceConnectionDB).where(
                SourceConnectionDB.connection_id == connection_id,
                SourceConnectionDB.tenant_id == tenant_id,
                SourceConnectionDB.project_id == project_id,
            )
        ).first()
        return (
            "connection_version_conflict"
            if row is not None
            else "connection_not_found"
        )

    @staticmethod
    def _index_failure(
        db: Session, tenant_id: str, project_id: str, knowledge_index_id: str
    ) -> str:
        row = db.exec(
            select(KnowledgeIndexSourceBindingDB).where(
                KnowledgeIndexSourceBindingDB.knowledge_index_id
                == knowledge_index_id,
                KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
                KnowledgeIndexSourceBindingDB.project_id == project_id,
            )
        ).first()
        return (
            "index_version_conflict"
            if row is not None
            else "knowledge_index_not_found"
        )


__all__ = ["SQLSourceIndexLifecycleRepository"]
