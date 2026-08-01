"""Concrete Hub adapters for the canonical source-control HTTP API."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    ActiveKnowledgeIndexEventDB,
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantDB,
    SourceControlBulkTargetCheckpointDB,
    SourceControlIndexReferenceDB,
    SourceControlJobEventOutboxDB,
    SourceConnectionDB,
    SourceControlOperationDB,
    SourceRevisionDB,
)
from agent.db_models.knowledge_index_execution import (
    KnowledgeIndexExecutionBindingDB,
)
from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.repositories.source_control_repository import (
    SQLSourceControlRepository,
)
from agent.services.effective_source_access_service import (
    EffectiveSourceAccessService,
)
from agent.services.context_policy_lifecycle import ContextPolicyActor
from agent.services.source_control_bulk_service import (
    BulkAuthorization,
    BulkIdempotencyClaim,
    BulkTargetCheckpoint,
    BulkTarget,
    SourceControlBulkService,
)
from agent.services.source_control_purge_approval import (
    SQLSourceControlPurgeApprovalStore,
)
from agent.services.source_control_artifact_download import (
    SourceControlArtifactStream,
)
from agent.services.source_control_job_events import (
    SourceControlJobEvent,
    SourceControlJobEventService,
)
from agent.services.source_control_grant_admin import (
    GrantAdminActor,
    GrantCreateRequest,
    GrantRevokeRequest,
)
from agent.services.source_control_projection_service import (
    SourceControlAggregateRecord,
    SourceControlPage,
    SourceControlPrincipal,
    SourceControlProjectionService,
)
from agent.services.source_index_lifecycle_service import (
    ActiveIndexPointer,
    PurgeBlocker,
    SourceIndexHistoryPage,
    SourceIndexLifecycleError,
    SourceIndexLifecycleScope,
    SourceIndexLifecycleService,
    SourceIndexRecord,
)
from agent.services.source_control_production_adapters import (
    ContainedArtifactDeletionService,
)
from ananta_contracts.source_control import (
    ConnectionState,
    ConnectorType,
    GrantOperation,
    GrantTransformation,
    Sensitivity,
    SourceConnection,
)


_LOG = logging.getLogger(__name__)
_SENSITIVE = frozenset({"secret", "credential", "security_sensitive"})


class SourceControlApiRuntimeError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


def _wire(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_wire(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _wire(to_dict())
    to_wire = getattr(value, "to_wire", None)
    if callable(to_wire):
        return _wire(to_wire())
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _wire(model_dump(mode="json", by_alias=True))
    if is_dataclass(value):
        return _wire(asdict(value))
    raise SourceControlApiRuntimeError(
        "source_control_projection_invalid", status_code=500
    )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _wire(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _cursor_encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _cursor_decode(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceControlApiRuntimeError("cursor_invalid") from exc
    if not decoded or len(decoded) > 160:
        raise SourceControlApiRuntimeError("cursor_invalid")
    return decoded


def _principal(value: object) -> SourceControlPrincipal:
    return SourceControlPrincipal(
        subject_id=str(getattr(value, "subject_id")),
        tenant_id=str(getattr(value, "tenant_id")),
        project_id=str(getattr(value, "project_id")),
        roles=frozenset(str(role) for role in getattr(value, "roles", ())),
    )


def _scope(value: object) -> SourceIndexLifecycleScope:
    actor = _principal(value)
    return SourceIndexLifecycleScope(
        tenant_id=actor.tenant_id,
        project_id=actor.project_id,
        actor_id=actor.subject_id,
        roles=actor.roles,
    )


class SQLSourceControlReadRepository:
    """Scoped read model for projections, object bindings and ETags."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def binding(
        self, *, resource_kind: str, resource_id: str
    ) -> Mapping[str, object] | None:
        model: type[Any]
        if resource_kind == "source_connection":
            model = SourceConnectionDB
        elif resource_kind == "source_revision":
            model = SourceRevisionDB
        elif resource_kind == "knowledge_index":
            model = KnowledgeIndexSourceBindingDB
        elif resource_kind == "context_policy":
            with Session(self._engine) as db:
                row = db.exec(
                    select(ContextPolicyVersionDB)
                    .where(
                        ContextPolicyVersionDB.policy_id == resource_id
                    )
                    .order_by(ContextPolicyVersionDB.version.desc())
                ).first()
                if row is None:
                    return None
                return {
                    "tenant_id": row.tenant_id,
                    "project_id": row.project_id,
                    "owner_id": row.created_by,
                }
        else:
            return None
        with Session(self._engine) as db:
            row = db.get(model, resource_id)
            if row is None:
                return None
            return {
                "tenant_id": row.tenant_id,
                "project_id": row.project_id,
                "owner_id": row.owner_id,
            }

    def list_aggregates(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, object],
    ) -> SourceControlPage:
        after_id = _cursor_decode(cursor)
        with Session(self._engine) as db:
            statement = select(SourceConnectionDB).where(
                SourceConnectionDB.tenant_id == tenant_id,
                SourceConnectionDB.project_id == project_id,
            )
            if after_id is not None:
                statement = statement.where(
                    SourceConnectionDB.connection_id > after_id
                )
            for name in ("state", "connector_type", "owner_id", "sensitivity"):
                if value := filters.get(name):
                    statement = statement.where(
                        getattr(SourceConnectionDB, name) == str(value)
                    )
            rows = list(
                db.exec(
                    statement.order_by(SourceConnectionDB.connection_id).limit(
                        limit + 1
                    )
                ).all()
            )
            selected = rows[:limit]
            return SourceControlPage(
                records=tuple(self._aggregate(db, row) for row in selected),
                next_cursor=(
                    _cursor_encode(selected[-1].connection_id)
                    if len(rows) > limit and selected
                    else None
                ),
            )

    def get_aggregate(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
    ) -> SourceControlAggregateRecord | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceConnectionDB).where(
                    SourceConnectionDB.connection_id == connection_id,
                    SourceConnectionDB.tenant_id == tenant_id,
                    SourceConnectionDB.project_id == project_id,
                )
            ).first()
            return None if row is None else self._aggregate(db, row)

    def connection_version(
        self, *, tenant_id: str, project_id: str, connection_id: str
    ) -> int:
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceConnectionDB).where(
                    SourceConnectionDB.connection_id == connection_id,
                    SourceConnectionDB.tenant_id == tenant_id,
                    SourceConnectionDB.project_id == project_id,
                )
            ).first()
            if row is None:
                raise SourceControlApiRuntimeError(
                    "source_control_not_found", status_code=404
                )
            return int(row.lock_version)

    def index_version(
        self, *, tenant_id: str, project_id: str, knowledge_index_id: str
    ) -> int:
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
                raise SourceControlApiRuntimeError(
                    "source_control_not_found", status_code=404
                )
            return int(row.lock_version)

    @staticmethod
    def index_etag(version: int) -> str:
        return f'"index:{version}"'

    def _aggregate(
        self, db: Session, connection: SourceConnectionDB
    ) -> SourceControlAggregateRecord:
        revisions = list(
            db.exec(
                select(SourceRevisionDB).where(
                    SourceRevisionDB.connection_id == connection.connection_id
                )
            ).all()
        )
        revision = max(
            revisions,
            key=lambda row: float(
                getattr(row, "created_at_epoch", 0.0)
                or getattr(row, "observed_at_epoch", 0.0)
            ),
            default=None,
        )
        indexes = list(
            db.exec(
                select(KnowledgeIndexSourceBindingDB).where(
                    KnowledgeIndexSourceBindingDB.connection_id
                    == connection.connection_id
                )
            ).all()
        )
        index = max(
            indexes,
            key=lambda row: float(row.updated_at_epoch),
            default=None,
        )
        active = db.exec(
            select(ActiveKnowledgeIndexDB).where(
                ActiveKnowledgeIndexDB.connection_id
                == connection.connection_id
            )
        ).first()
        grants = (
            list(
                db.exec(
                    select(SourceAccessGrantDB).where(
                        SourceAccessGrantDB.source_revision_id
                        == revision.source_revision_id
                    )
                ).all()
            )
            if revision is not None
            else []
        )
        return SourceControlAggregateRecord(
            connection_id=connection.connection_id,
            tenant_id=connection.tenant_id,
            project_id=connection.project_id,
            owner_id=connection.owner_id,
            version=int(connection.lock_version),
            connection={
                "connection_id": connection.connection_id,
                "project_id": connection.project_id,
                "connector_type": connection.connector_type,
                "display_name": connection.display_name,
                "sensitivity": connection.sensitivity,
                "state": connection.state,
            },
            revision=(
                {
                    "source_revision_id": revision.source_revision_id,
                    "revision_digest": revision.revision_digest,
                    "sensitivity": revision.sensitivity,
                }
                if revision is not None
                else None
            ),
            admission={
                "state": "admitted" if revision is not None else "pending"
            },
            index=(
                {
                    "knowledge_index_id": index.knowledge_index_id,
                    "source_revision_id": index.source_revision_id,
                    "status": index.status,
                    "policy_digest": index.policy_snapshot_digest,
                }
                if index is not None
                else None
            ),
            active_index=(
                {
                    "knowledge_index_id": active.knowledge_index_id,
                    "source_revision_id": active.source_revision_id,
                    "generation": active.generation,
                }
                if active is not None
                else None
            ),
            grants=tuple(
                {
                    "grant_id": grant.grant_id,
                    "destination_id": grant.destination_id,
                    "operation": grant.operation,
                    "transformation": grant.transformation,
                    "state": grant.state,
                }
                for grant in grants
            ),
            health={
                "status": (
                    "disabled"
                    if connection.state in {"disabled", "tombstoned"}
                    else "healthy"
                )
            },
            capabilities=frozenset(
                {
                    "refresh",
                    "scan",
                    "index",
                    "activate",
                    "grant",
                    "disable",
                    "rollback",
                }
            ),
            visible_subject_ids=frozenset(),
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
        after_id = _cursor_decode(cursor)
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
                    _cursor_encode(selected[-1].knowledge_index_id)
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
                _iso(run.completed_at_epoch) if run is not None else None
            ),
            tombstoned=row.status == "tombstoned",
            sensitive=bool(
                revision is not None and revision.sensitivity in _SENSITIVE
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


class SQLSourceControlJobEventRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def read_after(
        self,
        *,
        tenant_id: str,
        project_id: str,
        after_sequence: int,
        limit: int,
    ) -> Sequence[SourceControlJobEvent]:
        with Session(self._engine) as db:
            rows = list(
                db.exec(
                    select(SourceControlJobEventOutboxDB)
                    .where(
                        SourceControlJobEventOutboxDB.tenant_id == tenant_id,
                        SourceControlJobEventOutboxDB.project_id == project_id,
                        SourceControlJobEventOutboxDB.sequence
                        > after_sequence,
                    )
                    .order_by(SourceControlJobEventOutboxDB.sequence)
                    .limit(limit)
                ).all()
            )
        return tuple(
                SourceControlJobEvent(
                    event_id=row.event_id,
                    sequence=int(row.sequence or 0),
                    tenant_id=row.tenant_id,
                    project_id=row.project_id,
                    resource_id=row.resource_id,
                    job_id=row.job_id,
                    event_type=row.event_type,
                    status=row.status,
                    reason_code=row.reason_code,
                    trace_id=row.trace_id,
                    occurred_at=_iso(row.occurred_at_epoch) or "",
                )
                for row in rows
            )

    def append(
        self,
        *,
        event_id: str,
        tenant_id: str,
        project_id: str,
        resource_id: str,
        job_id: str,
        event_type: str,
        status: str,
        reason_code: str | None,
        trace_id: str,
        occurred_at_epoch: float,
    ) -> SourceControlJobEvent:
        with Session(self._engine) as db:
            row = SourceControlJobEventOutboxDB(
                event_id=event_id,
                tenant_id=tenant_id,
                project_id=project_id,
                resource_id=resource_id,
                job_id=job_id,
                event_type=event_type,
                status=status,
                reason_code=reason_code,
                trace_id=trace_id,
                occurred_at_epoch=float(occurred_at_epoch),
                created_at_epoch=time.time(),
            )
            db.add(row)
            try:
                db.commit()
                db.refresh(row)
            except IntegrityError:
                db.rollback()
                row = db.exec(
                    select(SourceControlJobEventOutboxDB).where(
                        SourceControlJobEventOutboxDB.event_id == event_id
                    )
                ).first()
                if row is None or (
                    row.tenant_id,
                    row.project_id,
                    row.resource_id,
                    row.job_id,
                    row.event_type,
                    row.status,
                    row.reason_code,
                    row.trace_id,
                ) != (
                    tenant_id,
                    project_id,
                    resource_id,
                    job_id,
                    event_type,
                    status,
                    reason_code,
                    trace_id,
                ):
                    raise SourceControlApiRuntimeError(
                        "job_event_id_conflict", status_code=409
                    ) from None
            return SourceControlJobEvent(
                event_id=row.event_id,
                sequence=int(row.sequence or 0),
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                resource_id=row.resource_id,
                job_id=row.job_id,
                event_type=row.event_type,
                status=row.status,
                reason_code=row.reason_code,
                trace_id=row.trace_id,
                occurred_at=_iso(row.occurred_at_epoch) or "",
            )


class SQLSourceControlOperationStore:
    """Lease/reclaim operation claim plus durable per-target checkpoints."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock=time.time,
        lease_seconds: float = 60.0,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("idempotency_lease_invalid")
        self._engine = engine
        self._clock = clock
        self._lease_seconds = float(lease_seconds)

    def claim(
        self, *, idempotency_key: str, plan_digest: str
    ) -> BulkIdempotencyClaim:
        now = float(self._clock())
        token = secrets.token_urlsafe(32)
        with Session(self._engine) as db:
            db.add(
                SourceControlOperationDB(
                    idempotency_key=idempotency_key,
                    request_digest=plan_digest,
                    operation="source_control_mutation",
                    state="claimed",
                    claim_token=token,
                    lease_expires_at_epoch=now + self._lease_seconds,
                    lock_version=1,
                    created_at_epoch=now,
                    updated_at_epoch=now,
                )
            )
            try:
                db.commit()
                return BulkIdempotencyClaim(
                    state="claimed",
                    claim_token=token,
                    lease_expires_at_epoch=now + self._lease_seconds,
                )
            except IntegrityError:
                db.rollback()
                row = db.get(SourceControlOperationDB, idempotency_key)
                if row is None:
                    raise SourceControlApiRuntimeError(
                        "idempotency_claim_failed", status_code=409
                    )
                if row.request_digest != plan_digest:
                    raise SourceControlApiRuntimeError(
                        "idempotency_key_conflict", status_code=409
                    )
                if row.state == "completed":
                    if not row.result_json:
                        raise SourceControlApiRuntimeError(
                            "idempotency_result_missing", status_code=500
                        )
                    return BulkIdempotencyClaim(
                        state="completed",
                        result=json.loads(row.result_json),
                    )
                if float(row.lease_expires_at_epoch or 0) <= now:
                    mutation = db.exec(
                        update(SourceControlOperationDB)
                        .where(
                            SourceControlOperationDB.idempotency_key
                            == idempotency_key,
                            SourceControlOperationDB.request_digest
                            == plan_digest,
                            SourceControlOperationDB.state == "claimed",
                            SourceControlOperationDB.lock_version
                            == row.lock_version,
                        )
                        .values(
                            claim_token=token,
                            lease_expires_at_epoch=now
                            + self._lease_seconds,
                            lock_version=row.lock_version + 1,
                            updated_at_epoch=now,
                        )
                    )
                    if mutation.rowcount == 1:
                        db.commit()
                        return BulkIdempotencyClaim(
                            state="claimed",
                            claim_token=token,
                            lease_expires_at_epoch=now
                            + self._lease_seconds,
                            checkpoints=self._checkpoints(
                                idempotency_key=idempotency_key,
                                plan_digest=plan_digest,
                            ),
                        )
                    db.rollback()
                return BulkIdempotencyClaim(state="in_progress")

    def complete(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str | None = None,
        result: Mapping[str, object],
    ) -> None:
        with Session(self._engine) as db:
            owner_filters = []
            if claim_token is not None:
                owner_filters.extend(
                    (
                        SourceControlOperationDB.claim_token == claim_token,
                        SourceControlOperationDB.lease_expires_at_epoch
                        > float(self._clock()),
                    )
                )
            mutation = db.exec(
                update(SourceControlOperationDB)
                .where(
                    SourceControlOperationDB.idempotency_key
                    == idempotency_key,
                    SourceControlOperationDB.request_digest == plan_digest,
                    SourceControlOperationDB.state == "claimed",
                    *owner_filters,
                )
                .values(
                    state="completed",
                    result_json=json.dumps(
                        dict(result),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                    lease_expires_at_epoch=None,
                    updated_at_epoch=float(self._clock()),
                )
            )
            if mutation.rowcount != 1:
                db.rollback()
                raise SourceControlApiRuntimeError(
                    "idempotency_completion_conflict", status_code=409
                )
            db.commit()

    def release(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str,
    ) -> None:
        """Expire an owned claim so a failed operation can be retried safely."""

        now = float(self._clock())
        with Session(self._engine) as db:
            mutation = db.exec(
                update(SourceControlOperationDB)
                .where(
                    SourceControlOperationDB.idempotency_key
                    == idempotency_key,
                    SourceControlOperationDB.request_digest == plan_digest,
                    SourceControlOperationDB.state == "claimed",
                    SourceControlOperationDB.claim_token == claim_token,
                )
                .values(
                    lease_expires_at_epoch=now,
                    updated_at_epoch=now,
                )
            )
            if mutation.rowcount != 1:
                db.rollback()
                raise SourceControlApiRuntimeError(
                    "idempotency_release_conflict", status_code=409
                )
            db.commit()

    def begin_target(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str,
        target_ordinal: int,
        resource_id: str,
        target_digest: str,
    ) -> BulkTargetCheckpoint:
        now = float(self._clock())
        checkpoint_id = "bcp_" + hashlib.sha256(
            f"{idempotency_key}\0{target_ordinal}\0{target_digest}".encode(
                "utf-8"
            )
        ).hexdigest()
        with Session(self._engine) as db:
            self._renew_owner(
                db,
                idempotency_key=idempotency_key,
                plan_digest=plan_digest,
                claim_token=claim_token,
                now=now,
            )
            existing = db.get(
                SourceControlBulkTargetCheckpointDB, checkpoint_id
            )
            if existing is None:
                existing = SourceControlBulkTargetCheckpointDB(
                    checkpoint_id=checkpoint_id,
                    idempotency_key=idempotency_key,
                    plan_digest=plan_digest,
                    target_ordinal=target_ordinal,
                    resource_id=resource_id,
                    target_digest=target_digest,
                    state="executing",
                    attempt_count=1,
                    created_at_epoch=now,
                    updated_at_epoch=now,
                )
                db.add(existing)
            elif (
                existing.idempotency_key != idempotency_key
                or existing.plan_digest != plan_digest
                or existing.target_ordinal != target_ordinal
                or existing.resource_id != resource_id
                or existing.target_digest != target_digest
            ):
                raise SourceControlApiRuntimeError(
                    "bulk_target_checkpoint_conflict", status_code=409
                )
            else:
                existing.attempt_count += 1
                existing.updated_at_epoch = now
                db.add(existing)
            db.commit()
            db.refresh(existing)
            return self._checkpoint(existing)

    def complete_target(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str,
        target_ordinal: int,
        target_digest: str,
        result: Mapping[str, object],
    ) -> BulkTargetCheckpoint:
        now = float(self._clock())
        encoded = json.dumps(
            dict(result),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        with Session(self._engine) as db:
            self._renew_owner(
                db,
                idempotency_key=idempotency_key,
                plan_digest=plan_digest,
                claim_token=claim_token,
                now=now,
            )
            row = db.exec(
                select(SourceControlBulkTargetCheckpointDB).where(
                    SourceControlBulkTargetCheckpointDB.idempotency_key
                    == idempotency_key,
                    SourceControlBulkTargetCheckpointDB.plan_digest
                    == plan_digest,
                    SourceControlBulkTargetCheckpointDB.target_ordinal
                    == target_ordinal,
                    SourceControlBulkTargetCheckpointDB.target_digest
                    == target_digest,
                )
            ).first()
            if row is None:
                raise SourceControlApiRuntimeError(
                    "bulk_target_checkpoint_missing", status_code=409
                )
            if row.state == "completed":
                if row.result_json != encoded:
                    raise SourceControlApiRuntimeError(
                        "bulk_target_result_conflict", status_code=409
                    )
                return self._checkpoint(row)
            row.state = "completed"
            row.result_json = encoded
            row.updated_at_epoch = now
            db.add(row)
            db.commit()
            db.refresh(row)
            return self._checkpoint(row)

    def _checkpoints(
        self, *, idempotency_key: str, plan_digest: str
    ) -> tuple[BulkTargetCheckpoint, ...]:
        with Session(self._engine) as db:
            rows = db.exec(
                select(SourceControlBulkTargetCheckpointDB)
                .where(
                    SourceControlBulkTargetCheckpointDB.idempotency_key
                    == idempotency_key,
                    SourceControlBulkTargetCheckpointDB.plan_digest
                    == plan_digest,
                )
                .order_by(
                    SourceControlBulkTargetCheckpointDB.target_ordinal
                )
            ).all()
            return tuple(self._checkpoint(row) for row in rows)

    def _renew_owner(
        self,
        db: Session,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str,
        now: float,
    ) -> None:
        mutation = db.exec(
            update(SourceControlOperationDB)
            .where(
                SourceControlOperationDB.idempotency_key == idempotency_key,
                SourceControlOperationDB.request_digest == plan_digest,
                SourceControlOperationDB.state == "claimed",
                SourceControlOperationDB.claim_token == claim_token,
                SourceControlOperationDB.lease_expires_at_epoch > now,
            )
            .values(
                lease_expires_at_epoch=now + self._lease_seconds,
                updated_at_epoch=now,
            )
        )
        if mutation.rowcount != 1:
            db.rollback()
            raise SourceControlApiRuntimeError(
                "idempotency_claim_lost", status_code=409
            )

    @staticmethod
    def _checkpoint(
        row: SourceControlBulkTargetCheckpointDB,
    ) -> BulkTargetCheckpoint:
        return BulkTargetCheckpoint(
            target_ordinal=int(row.target_ordinal),
            resource_id=row.resource_id,
            target_digest=row.target_digest,
            state=row.state,
            result=(
                json.loads(row.result_json) if row.result_json else None
            ),
        )


class _LifecycleAudit:
    def record(self, **event: object) -> None:
        _LOG.info(
            "source_control_lifecycle_audit %s",
            json.dumps(_wire(event), sort_keys=True, ensure_ascii=True),
        )


class _BulkAuthorization:
    def __init__(
        self,
        projection: SourceControlProjectionService,
        principal: SourceControlPrincipal,
    ) -> None:
        self._projection = projection
        self._principal = principal

    def authorize(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        mutation: str,
        target: BulkTarget,
    ) -> BulkAuthorization:
        if (
            tenant_id != self._principal.tenant_id
            or project_id != self._principal.project_id
            or actor_id != self._principal.subject_id
        ):
            return BulkAuthorization(False, "scope_mismatch", "")
        try:
            projection = self._projection.get(
                principal=self._principal,
                connection_id=target.resource_id,
            )
        except Exception:
            return BulkAuthorization(False, "source_control_not_found", "")
        actions = set(getattr(projection, "next_actions", ()))
        allowed = mutation in actions or (
            mutation == "disable" and "disable" in actions
        )
        return BulkAuthorization(
            allowed=allowed,
            reason_code="authorized" if allowed else "policy_denied",
            current_etag=projection.etag,
        )


class _BulkMutation:
    def __init__(
        self,
        *,
        lifecycle: SourceIndexLifecycleService,
        reads: SQLSourceControlReadRepository,
        principal: SourceControlPrincipal,
        operations: object | None,
    ) -> None:
        self._lifecycle = lifecycle
        self._reads = reads
        self._principal = principal
        self._operations = operations

    def execute(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        mutation: str,
        target: BulkTarget,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        if mutation == "disable":
            version = self._reads.connection_version(
                tenant_id=tenant_id,
                project_id=project_id,
                connection_id=target.resource_id,
            )
            updated = self._lifecycle.disable(
                scope=SourceIndexLifecycleScope(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    actor_id=actor_id,
                    roles=self._principal.roles,
                ),
                connection_id=target.resource_id,
                expected_version=version,
            )
            return {"status": "completed", "version": updated}
        execute = getattr(self._operations, "execute", None)
        if callable(execute):
            return dict(
                execute(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    actor_id=actor_id,
                    mutation=mutation,
                    resource_id=target.resource_id,
                    idempotency_key=idempotency_key,
                )
            )
        return {
            "status": "failed",
            "reason_code": "source_control_operation_unavailable",
        }


@dataclass(frozen=True)
class SourceControlApiRuntime:
    engine: Engine
    reads: SQLSourceControlReadRepository
    projection: SourceControlProjectionService
    lifecycle: SourceIndexLifecycleService
    events: SourceControlJobEventService
    idempotency: SQLSourceControlOperationStore
    access: object | None = None
    operations: object | None = None
    context_policy: object | None = None
    artifact_deletion: ContainedArtifactDeletionService | None = None
    content_admission: object | None = None
    catalogs: object | None = None
    grants: object | None = None
    connection_intents: object | None = None
    codehug_mutations: object | None = None
    artifact_downloads: object | None = None

    def binding(
        self, *, resource_kind: str, resource_id: str
    ) -> Mapping[str, object] | None:
        return self.reads.binding(
            resource_kind=resource_kind, resource_id=resource_id
        )

    def validate_connection(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        contract = self._connection_contract(principal, payload)
        return {"valid": True, "connection": contract.to_wire()}

    def create_connection(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        contract, resolved = self._resolved_connection(principal, payload)
        request_digest = _digest(
            {
                "operation": "create_connection",
                "scope": [actor.tenant_id, actor.project_id, actor.subject_id],
                "connection": contract.to_wire(),
            }
        )
        key = _operation_key(
            "create", actor.tenant_id, idempotency_key
        )
        claim = self.idempotency.claim(
            idempotency_key=key, plan_digest=request_digest
        )
        if claim.state == "completed":
            return dict(claim.result or {})
        if claim.state == "in_progress":
            raise SourceControlApiRuntimeError(
                "idempotency_in_progress", status_code=409
            )
        record = SQLSourceControlRepository(
            self.engine
        ).save_connection_with_selector(
            contract,
            resolved.binding(
                connection_id=contract.connection_id,
                tenant_id=contract.tenant_id,
                project_id=contract.project_id,
                owner_id=contract.owner_id,
            ),
        )
        result = {
            "connection": record.contract.to_wire(),
            "version": int(record.lock_version),
        }
        self.idempotency.complete(
            idempotency_key=key,
            plan_digest=request_digest,
            result=result,
        )
        return result

    def validate_content_admission(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        return dict(
            self._content_admission().validate(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                actor_id=actor.subject_id,
                payload=payload,
            )
        )

    def create_content_admission(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        return dict(
            self._content_admission().admit(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                actor_id=actor.subject_id,
                payload=payload,
                idempotency_key=idempotency_key,
            )
        )

    def list_source_control_catalog(
        self,
        *,
        principal: object,
        catalog: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        if project_id != actor.project_id:
            raise SourceControlApiRuntimeError(
                "source_control_project_scope_mismatch",
                status_code=403,
            )
        service = self._catalog_service()
        if catalog == "workspaces":
            return dict(
                service.list_workspaces(
                    tenant_id=actor.tenant_id,
                    project_id=actor.project_id,
                    actor_id=actor.subject_id,
                    roles=actor.roles,
                    cursor=cursor,
                    limit=limit,
                    filters=filters,
                )
            )
        if catalog == "registered_remotes":
            return dict(
                service.list_registered_remotes(
                    tenant_id=actor.tenant_id,
                    project_id=actor.project_id,
                    actor_id=actor.subject_id,
                    roles=actor.roles,
                    cursor=cursor,
                    limit=limit,
                    filters=filters,
                )
            )
        if catalog == "index_profiles":
            return dict(
                service.list_index_profiles(
                    project_id=actor.project_id,
                    cursor=cursor,
                    limit=limit,
                    filters=filters,
                )
            )
        raise SourceControlApiRuntimeError("source_control_catalog_invalid")

    def list_grant_presets(
        self,
        *,
        principal: object,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        _require_project_scope(actor=actor, project_id=project_id)
        _validate_grant_query(cursor=cursor, filters=filters)
        q = filters.get("q", "").casefold()
        operation = filters.get("operation")
        transformation = filters.get("transformation")
        if operation is not None:
            try:
                GrantOperation(operation)
            except ValueError as exc:
                raise SourceControlApiRuntimeError(
                    "grant_operation_filter_invalid"
                ) from exc
        if transformation is not None:
            try:
                GrantTransformation(transformation)
            except ValueError as exc:
                raise SourceControlApiRuntimeError(
                    "grant_transformation_filter_invalid"
                ) from exc
        presets = [
            preset
            for preset in self._grant_service().list_presets(
                actor=_grant_actor(actor)
            )
            if (
                not q
                or q
                in " ".join(
                    (
                        preset.preset_id,
                        preset.label,
                        preset.description,
                        preset.purpose,
                    )
                ).casefold()
            )
            and (
                operation is None
                or preset.operation.value == operation
            )
            and (
                transformation is None
                or preset.transformation.value == transformation
            )
        ]
        after = _decode_grant_preset_cursor(cursor)
        start = 0
        if after is not None:
            positions = {
                preset.preset_id: index
                for index, preset in enumerate(presets)
            }
            if after not in positions:
                raise SourceControlApiRuntimeError(
                    "grant_preset_cursor_invalid"
                )
            start = positions[after] + 1
        visible = presets[start : start + limit]
        has_more = start + limit < len(presets)
        return {
            "items": [_wire(preset) for preset in visible],
            "next_cursor": (
                _encode_grant_preset_cursor(visible[-1].preset_id)
                if has_more and visible
                else None
            ),
            "capabilities": _grant_preset_capabilities(actor.project_id),
        }

    def list_grants(
        self,
        *,
        principal: object,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        _require_project_scope(actor=actor, project_id=project_id)
        _validate_grant_query(cursor=cursor, filters=filters)
        page = self._grant_service().list_grants(
            actor=_grant_actor(actor),
            cursor=cursor,
            limit=limit,
            state=filters.get("state"),
            source_revision_id=filters.get("source_revision_id"),
            destination_id=filters.get("destination_id"),
        )
        result = dict(_wire(page))
        result["capabilities"] = _grant_capabilities(actor.project_id)
        return result

    def create_grant(
        self,
        *,
        principal: object,
        project_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        _require_project_scope(actor=actor, project_id=project_id)
        grant = self._grant_service().create_grant(
            actor=_grant_actor(actor),
            request=GrantCreateRequest.from_mapping(payload),
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        return {
            "grant": _wire(grant),
            "capabilities": _grant_capabilities(actor.project_id),
        }

    def revoke_grant(
        self,
        *,
        principal: object,
        project_id: str,
        grant_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        _require_project_scope(actor=actor, project_id=project_id)
        grant = self._grant_service().revoke_grant(
            actor=_grant_actor(actor),
            grant_id=grant_id,
            request=GrantRevokeRequest.from_mapping(payload),
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        return {
            "grant": _wire(grant),
            "capabilities": _grant_capabilities(actor.project_id),
        }

    def list_connections(
        self,
        *,
        principal: object,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        return _wire(
            self.projection.list(
                principal=_principal(principal),
                cursor=cursor,
                limit=limit,
                filters=filters,
            )
        )

    def get_connection(
        self, *, principal: object, connection_id: str
    ) -> tuple[Mapping[str, object], str]:
        projection = self.projection.get(
            principal=_principal(principal),
            connection_id=connection_id,
        )
        return _wire(projection), projection.etag

    def run_history(
        self,
        *,
        principal: object,
        connection_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        value = _wire(
            self.lifecycle.history(
                scope=_scope(principal),
                connection_id=connection_id,
                cursor=cursor,
                limit=limit,
            )
        )
        for item in value.get("items", []):
            version = self.reads.index_version(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                knowledge_index_id=str(item["knowledge_index_id"]),
            )
            item["etag"] = self.reads.index_etag(version)
        return value

    def compare_indices(
        self,
        *,
        principal: object,
        left_index_id: str,
        right_index_id: str,
    ) -> Mapping[str, object]:
        return self.lifecycle.compare(
            scope=_scope(principal),
            left_index_id=left_index_id,
            right_index_id=right_index_id,
        )

    def mutate(
        self,
        *,
        principal: object,
        operation: str,
        resource_id: str,
        if_match: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        request_digest = _digest(
            {
                "operation": operation,
                "resource_id": resource_id,
                "if_match": if_match,
                "payload": payload,
                "scope": [actor.tenant_id, actor.project_id, actor.subject_id],
            }
        )
        key = _operation_key(
            "lifecycle", actor.tenant_id, idempotency_key
        )
        claim = self.idempotency.claim(
            idempotency_key=key, plan_digest=request_digest
        )
        if claim.state == "completed":
            return dict(claim.result or {})
        if claim.state == "in_progress":
            raise SourceControlApiRuntimeError(
                "idempotency_in_progress", status_code=409
            )
        scope = _scope(principal)
        if operation in {"activate", "rollback"}:
            generation = _etag_number(if_match, "active")
            pointer = (
                self.lifecycle.activate(
                    scope=scope,
                    knowledge_index_id=resource_id,
                    expected_generation=generation,
                )
                if operation == "activate"
                else self.lifecycle.rollback(
                    scope=scope,
                    target_index_id=resource_id,
                    expected_generation=generation,
                )
            )
            result: dict[str, object] = {
                "operation": operation,
                "resource_id": resource_id,
                "result": _wire(pointer),
            }
        elif operation == "disable":
            projection = self.projection.get(
                principal=actor, connection_id=resource_id
            )
            self.projection.assert_if_match(projection, if_match)
            version = self.reads.connection_version(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                connection_id=resource_id,
            )
            updated = self.lifecycle.disable(
                scope=scope,
                connection_id=resource_id,
                expected_version=version,
            )
            result = {
                "operation": operation,
                "resource_id": resource_id,
                "result": {"version": updated},
            }
        elif operation == "tombstone":
            version = self._assert_index_etag(actor, resource_id, if_match)
            updated = self.lifecycle.tombstone(
                scope=scope,
                knowledge_index_id=resource_id,
                expected_version=version,
            )
            result = {
                "operation": operation,
                "resource_id": resource_id,
                "result": {
                    "version": updated,
                    "etag": self.reads.index_etag(updated),
                },
            }
        elif operation == "purge":
            version = self._assert_index_etag(actor, resource_id, if_match)
            approval_id = payload.get("approval_id")
            if approval_id is not None and not isinstance(approval_id, str):
                raise SourceControlApiRuntimeError("approval_id_invalid")
            self.lifecycle.purge(
                scope=scope,
                knowledge_index_id=resource_id,
                expected_version=version,
                approval_id=approval_id,
                approval_claim_id=key,
            )
            result = {
                "operation": operation,
                "resource_id": resource_id,
                "result": {"purged": True},
            }
        else:
            raise SourceControlApiRuntimeError(
                "source_control_operation_invalid"
            )
        self.idempotency.complete(
            idempotency_key=key,
            plan_digest=request_digest,
            result=result,
        )
        return result

    def dispatch_operation(
        self,
        *,
        principal: object,
        operation: str,
        connection_id: str,
        if_match: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        if operation == "run":
            self._catalog_service().require_index_profile(
                project_id=actor.project_id,
                profile_id=str(payload.get("index_profile_id") or ""),
            )
        projection = self.projection.get(
            principal=actor, connection_id=connection_id
        )
        self.projection.assert_if_match(projection, if_match)
        execute = getattr(self.operations, operation, None)
        if not callable(execute):
            execute = getattr(self.operations, "execute", None)
        if not callable(execute):
            raise SourceControlApiRuntimeError(
                "source_control_operation_unavailable", status_code=503
            )
        request_digest = _digest(
            {
                "operation": operation,
                "scope": [actor.tenant_id, actor.project_id, actor.subject_id],
                "connection_id": connection_id,
                "if_match": if_match,
                "payload": payload,
            }
        )
        key = _operation_key(
            "operation", actor.tenant_id, idempotency_key
        )
        claim = self.idempotency.claim(
            idempotency_key=key, plan_digest=request_digest
        )
        if claim.state == "completed":
            return dict(claim.result or {})
        if claim.state == "in_progress":
            raise SourceControlApiRuntimeError(
                "idempotency_in_progress", status_code=409
            )
        kwargs = {
            "tenant_id": actor.tenant_id,
            "project_id": actor.project_id,
            "actor_id": actor.subject_id,
            "connection_id": connection_id,
            "payload": dict(payload),
            "idempotency_key": idempotency_key,
        }
        if not claim.claim_token:
            raise SourceControlApiRuntimeError(
                "idempotency_claim_token_missing", status_code=500
            )
        try:
            if getattr(self.operations, operation, None) is execute:
                raw = execute(**kwargs)
            else:
                raw = execute(operation=operation, **kwargs)
        except Exception:
            try:
                self.idempotency.release(
                    idempotency_key=key,
                    plan_digest=request_digest,
                    claim_token=claim.claim_token,
                )
            except Exception:
                pass
            raise
        result = {
            "operation": operation,
            "connection_id": connection_id,
            "receipt": _wire(raw),
        }
        self.idempotency.complete(
            idempotency_key=key,
            plan_digest=request_digest,
            claim_token=claim.claim_token,
            result=result,
        )
        return result

    def graph(
        self,
        *,
        principal: object,
        connection_id: str,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self._read_operation(
            principal=principal,
            operation="graph",
            connection_id=connection_id,
            parameters=parameters,
        )

    def query(
        self,
        *,
        principal: object,
        connection_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self._read_operation(
            principal=principal,
            operation="query",
            connection_id=connection_id,
            parameters=payload,
        )

    def artifact_status(
        self,
        *,
        principal: object,
        connection_id: str,
        artifact_id: str,
    ) -> Mapping[str, object]:
        return self._read_operation(
            principal=principal,
            operation="artifact_status",
            connection_id=connection_id,
            parameters={"artifact_id": artifact_id},
        )

    def bulk_plan(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        mutation, targets = _bulk_request(payload)
        actor = _principal(principal)
        return _wire(
            self._bulk(actor).plan(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                actor_id=actor.subject_id,
                mutation=mutation,
                targets=targets,
                dry_run=True,
            )
        )

    def bulk_execute(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        plan_payload = payload.get("plan")
        if not isinstance(plan_payload, Mapping):
            raise SourceControlApiRuntimeError("bulk_plan_invalid")
        mutation, targets = _bulk_plan_replay(plan_payload)
        actor = _principal(principal)
        service = self._bulk(actor)
        plan = service.plan(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            actor_id=actor.subject_id,
            mutation=mutation,
            targets=targets,
            dry_run=True,
        )
        supplied = payload.get("supplied_plan_digest")
        if not isinstance(supplied, str):
            raise SourceControlApiRuntimeError("bulk_plan_digest_invalid")
        result = dict(
            service.execute(
                plan=plan,
                supplied_plan_digest=supplied,
                idempotency_key=_operation_key(
                    "bulk", actor.tenant_id, idempotency_key
                ),
            )
        )
        result.pop("schema", None)
        return result

    def poll_events(
        self,
        *,
        principal: object,
        after_sequence: int,
        limit: int,
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        return self.events.poll(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def access_preview(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        access = self._effective_access(actor)
        return _wire(
            access.preview(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                source_revision_id=str(payload["source_revision_id"]),
                destination_id=str(payload["destination_id"]),
                operation=GrantOperation(str(payload["operation"])),
                transformation=GrantTransformation(
                    str(payload["transformation"])
                ),
                purpose=str(payload["purpose"]),
            )
        )

    def access_matrix(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        access = self._effective_access(actor)
        value = _wire(
            access.matrix(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                operation=GrantOperation(str(payload["operation"])),
                transformation=GrantTransformation(
                    str(payload["transformation"])
                ),
                purpose=str(payload["purpose"]),
                source_cursor=payload.get("source_cursor"),
                destination_cursor=payload.get("destination_cursor"),
                source_limit=int(payload.get("source_limit", 25)),
                destination_limit=int(payload.get("destination_limit", 25)),
                source_filters=payload.get("source_filters") or {},
                destination_filters=payload.get("destination_filters") or {},
            )
        )
        return {
            "items": value.get("items", value.get("rows", [])),
            "source_next_cursor": value.get(
                "source_next_cursor",
                value.get("next_source_cursor"),
            ),
            "destination_next_cursor": value.get(
                "destination_next_cursor",
                value.get("next_destination_cursor"),
            ),
        }

    def context_policy_list(
        self, *, principal: object, cursor: str | None, limit: int
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        after = _cursor_decode(cursor)
        with Session(self.engine) as db:
            rows = list(
                db.exec(
                    select(ContextPolicyVersionDB)
                    .where(
                        ContextPolicyVersionDB.tenant_id == actor.tenant_id,
                        ContextPolicyVersionDB.project_id == actor.project_id,
                    )
                    .order_by(
                        ContextPolicyVersionDB.policy_id,
                        ContextPolicyVersionDB.version.desc(),
                    )
                ).all()
            )
        latest: dict[str, ContextPolicyVersionDB] = {}
        for row in rows:
            if after is not None and row.policy_id <= after:
                continue
            latest.setdefault(row.policy_id, row)
        selected = list(latest.values())[: limit + 1]
        visible = selected[:limit]
        return {
            "items": [
                {
                    "policy_id": row.policy_id,
                    "latest_version": row.version,
                    "state": row.state,
                    "etag": row.etag,
                    "policy_digest": row.policy_digest,
                }
                for row in visible
            ],
            "next_cursor": (
                _cursor_encode(visible[-1].policy_id)
                if len(selected) > limit and visible
                else None
            ),
        }

    def context_policy_versions(
        self,
        *,
        principal: object,
        policy_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, object]:
        service = self._context_policy()
        items, next_cursor = service.versions(
            actor=_context_actor(principal),
            policy_id=policy_id,
            cursor=cursor,
            limit=limit,
        )
        return {
            "items": _wire(items),
            "next_cursor": next_cursor,
        }

    def context_policy_detail(
        self, *, principal: object, policy_id: str, version: int
    ) -> tuple[Mapping[str, object], str]:
        item = self._context_policy().detail(
            actor=_context_actor(principal),
            policy_id=policy_id,
            version=version,
        )
        return _wire(item), str(item.etag)

    def context_policy_active(
        self, *, principal: object, policy_id: str
    ) -> tuple[Mapping[str, object], str]:
        item = self._context_policy().active(
            actor=_context_actor(principal),
            policy_id=policy_id,
        )
        return _wire(item), str(item.etag)

    def context_policy_draft(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        item = self._context_policy().create_draft(
            actor=_context_actor(principal),
            policy_id=policy_id,
            document=dict(payload["document"]),
            expected_latest_version=payload.get("expected_latest_version"),
            idempotency_key=idempotency_key,
        )
        return _wire(item)

    def context_policy_lint(
        self, *, principal: object, policy_id: str, version: int
    ) -> Mapping[str, object]:
        diagnostics = self._context_policy().lint(
            actor=_context_actor(principal),
            policy_id=policy_id,
            version=version,
        )
        return {"diagnostics": _wire(diagnostics)}

    def context_policy_preview(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        preview = self._context_policy().preview(
            actor=_context_actor(principal),
            policy_id=policy_id,
            version=int(payload["version"]),
            source_revision_id=str(payload["source_revision_id"]),
            destination_id=str(payload["destination_id"]),
            operation=GrantOperation(str(payload["operation"])),
            transformation=GrantTransformation(
                str(payload["transformation"])
            ),
        )
        return _wire(preview)

    def context_policy_transition(
        self,
        *,
        principal: object,
        operation: str,
        policy_id: str,
        version: int,
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        service = self._context_policy()
        method = (
            service.activate if operation == "activate" else service.revoke
        )
        return _wire(
            method(
                actor=_context_actor(principal),
                policy_id=policy_id,
                version=version,
                if_match=if_match,
                idempotency_key=idempotency_key,
            )
        )

    def context_policy_rollback(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        service = self._context_policy()
        versions, _ = service.versions(
            actor=_context_actor(principal),
            policy_id=policy_id,
            limit=1,
        )
        if not versions or versions[0].etag != if_match.strip('"'):
            raise SourceControlApiRuntimeError(
                "policy_version_conflict", status_code=412
            )
        return _wire(
            service.rollback(
                actor=_context_actor(principal),
                policy_id=policy_id,
                target_version=int(payload["target_version"]),
                expected_latest_version=int(
                    payload["expected_latest_version"]
                ),
                idempotency_key=idempotency_key,
            )
        )

    def _bulk(
        self, actor: SourceControlPrincipal
    ) -> SourceControlBulkService:
        return SourceControlBulkService(
            authorization=_BulkAuthorization(self.projection, actor),
            mutations=_BulkMutation(
                lifecycle=self.lifecycle,
                reads=self.reads,
                principal=actor,
                operations=self.operations,
            ),
            idempotency=self.idempotency,
        )

    def _assert_index_etag(
        self,
        actor: SourceControlPrincipal,
        knowledge_index_id: str,
        if_match: str,
    ) -> int:
        version = self.reads.index_version(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            knowledge_index_id=knowledge_index_id,
        )
        if if_match != self.reads.index_etag(version):
            raise SourceControlApiRuntimeError(
                "index_version_conflict", status_code=412
            )
        return version

    def artifact_download(
        self,
        *,
        principal: object,
        connection_id: str,
        artifact_id: str,
        range_header: str | None,
    ) -> SourceControlArtifactStream:
        if self.artifact_downloads is None:
            raise SourceControlApiRuntimeError(
                "artifact_download_unavailable", status_code=503
            )
        self.projection.get(
            principal=_principal(principal), connection_id=connection_id
        )
        open_stream = getattr(self.artifact_downloads, "open", None)
        if not callable(open_stream):
            raise SourceControlApiRuntimeError(
                "artifact_download_unavailable", status_code=503
            )
        stream = open_stream(
            principal=principal,
            connection_id=connection_id,
            artifact_id=artifact_id,
            range_header=range_header,
        )
        if not isinstance(stream, SourceControlArtifactStream):
            raise SourceControlApiRuntimeError(
                "artifact_download_result_invalid", status_code=502
            )
        return stream

    def codehug_mutation(
        self,
        *,
        principal: object,
        mutation_intent_id: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        if self.codehug_mutations is None:
            raise SourceControlApiRuntimeError(
                "codehug_mutation_unavailable", status_code=503
            )
        actor = _principal(principal)
        request_digest = _digest(
            {
                "operation": "codehug_mutation",
                "scope": [
                    actor.tenant_id,
                    actor.project_id,
                    actor.subject_id,
                ],
                "mutation_intent_id": mutation_intent_id,
            }
        )
        key = _operation_key(
            "codehug", actor.tenant_id, idempotency_key
        )
        claim = self.idempotency.claim(
            idempotency_key=key, plan_digest=request_digest
        )
        if claim.state == "completed":
            return dict(claim.result or {})
        if claim.state == "in_progress":
            raise SourceControlApiRuntimeError(
                "idempotency_in_progress", status_code=409
            )
        execute = getattr(self.codehug_mutations, "execute", None)
        if not callable(execute):
            raise SourceControlApiRuntimeError(
                "codehug_mutation_unavailable", status_code=503
            )
        result = dict(
            execute(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                actor_id=actor.subject_id,
                mutation_intent_id=mutation_intent_id,
            )
        )
        self.idempotency.complete(
            idempotency_key=key,
            plan_digest=request_digest,
            result=result,
        )
        return result

    def _connection_contract(
        self, principal: object, payload: Mapping[str, object]
    ) -> SourceConnection:
        return self._resolved_connection(principal, payload)[0]

    def _resolved_connection(
        self, principal: object, payload: Mapping[str, object]
    ) -> tuple[SourceConnection, object]:
        actor = _principal(principal)
        if self.connection_intents is None:
            raise SourceControlApiRuntimeError(
                "source_control_connection_catalog_unavailable",
                status_code=503,
            )
        try:
            resolved = self.connection_intents.resolve(
                principal=actor,
                payload=payload,
            )
            contract = SourceConnection.create(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                owner_id=actor.subject_id,
                connector_type=ConnectorType(
                    str(resolved.connector_type)
                ),
                connection_identity_digest=str(
                    resolved.connection_identity_digest
                ),
                display_name=str(resolved.display_name),
                sensitivity=Sensitivity(str(resolved.sensitivity)),
                state=ConnectionState.DRAFT,
                created_at=datetime.now(timezone.utc),
            )
            return contract, resolved
        except (KeyError, TypeError, ValueError) as exc:
            reason_code = str(getattr(exc, "reason_code", "") or "")
            if reason_code:
                raise SourceControlApiRuntimeError(
                    reason_code,
                    status_code=int(getattr(exc, "status_code", 400)),
                ) from exc
            raise SourceControlApiRuntimeError(
                "source_connection_invalid"
            ) from exc

    def _read_operation(
        self,
        *,
        principal: object,
        operation: str,
        connection_id: str,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        actor = _principal(principal)
        self.projection.get(
            principal=actor, connection_id=connection_id
        )
        method = getattr(self.operations, operation, None)
        if not callable(method):
            raise SourceControlApiRuntimeError(
                "source_control_operation_unavailable", status_code=503
            )
        value = _wire(
            method(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                actor_id=actor.subject_id,
                connection_id=connection_id,
                parameters=dict(parameters),
            )
        )
        if not isinstance(value, dict):
            raise SourceControlApiRuntimeError(
                "source_control_operation_result_invalid",
                status_code=502,
            )
        value.setdefault("text_alternative", "")
        value.setdefault(
            "artifact_status",
            {"state": "not_applicable", "reason_code": None},
        )
        return value

    def _context_policy(self):
        if self.context_policy is None:
            raise SourceControlApiRuntimeError(
                "context_policy_lifecycle_unavailable", status_code=503
            )
        return self.context_policy

    def _effective_access(
        self, actor: SourceControlPrincipal
    ) -> EffectiveSourceAccessService:
        if self.access is None:
            raise SourceControlApiRuntimeError(
                "effective_source_access_unavailable", status_code=503
            )
        if callable(self.access):
            service = self.access(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
            )
        else:
            service = self.access
        if not isinstance(service, EffectiveSourceAccessService):
            raise SourceControlApiRuntimeError(
                "effective_source_access_invalid", status_code=500
            )
        return service

    def _content_admission(self):
        if self.content_admission is None:
            raise SourceControlApiRuntimeError(
                "content_admission_unavailable", status_code=503
            )
        return self.content_admission

    def _catalog_service(self):
        if self.catalogs is None:
            raise SourceControlApiRuntimeError(
                "source_control_catalog_unavailable", status_code=503
            )
        return self.catalogs

    def _grant_service(self):
        if self.grants is None:
            raise SourceControlApiRuntimeError(
                "source_control_grant_admin_unavailable", status_code=503
            )
        return self.grants


def _etag_number(value: str, namespace: str) -> int:
    normalized = value.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:]
    normalized = normalized.strip('"')
    prefix = f"{namespace}:"
    if normalized.startswith(prefix):
        normalized = normalized[len(prefix) :]
    try:
        parsed = int(normalized)
    except ValueError as exc:
        raise SourceControlApiRuntimeError(
            "if_match_invalid", status_code=412
        ) from exc
    if parsed < 0:
        raise SourceControlApiRuntimeError(
            "if_match_invalid", status_code=412
        )
    return parsed


def _bulk_request(
    payload: Mapping[str, object],
) -> tuple[str, tuple[BulkTarget, ...]]:
    if set(payload) - {"mutation", "targets", "dry_run"}:
        raise SourceControlApiRuntimeError("bulk_request_fields_forbidden")
    mutation = payload.get("mutation")
    raw_targets = payload.get("targets")
    if not isinstance(mutation, str) or not isinstance(raw_targets, list):
        raise SourceControlApiRuntimeError("bulk_request_invalid")
    targets: list[BulkTarget] = []
    for raw in raw_targets:
        if not isinstance(raw, Mapping) or set(raw) != {
            "resource_id",
            "expected_etag",
        }:
            raise SourceControlApiRuntimeError("bulk_target_invalid")
        targets.append(
            BulkTarget(
                resource_id=str(raw["resource_id"]),
                expected_etag=str(raw["expected_etag"]),
            )
        )
    return mutation, tuple(targets)


def _bulk_plan_replay(
    plan: Mapping[str, object],
) -> tuple[str, tuple[BulkTarget, ...]]:
    mutation = plan.get("mutation")
    items = plan.get("items")
    if not isinstance(mutation, str) or not isinstance(items, list):
        raise SourceControlApiRuntimeError("bulk_plan_invalid")
    targets: list[BulkTarget] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise SourceControlApiRuntimeError("bulk_plan_invalid")
        targets.append(
            BulkTarget(
                resource_id=str(item.get("resource_id", "")),
                expected_etag=str(item.get("expected_etag", "")),
            )
        )
    return mutation, tuple(targets)


def _context_actor(value: object) -> ContextPolicyActor:
    actor = _principal(value)
    return ContextPolicyActor(
        subject_id=actor.subject_id,
        tenant_id=actor.tenant_id,
        project_id=actor.project_id,
        roles=actor.roles,
    )


def _grant_actor(value: object) -> GrantAdminActor:
    actor = _principal(value)
    return GrantAdminActor(
        subject_id=actor.subject_id,
        tenant_id=actor.tenant_id,
        project_id=actor.project_id,
        roles=actor.roles,
    )


def _require_project_scope(
    *, actor: SourceControlPrincipal, project_id: str
) -> None:
    if project_id != actor.project_id:
        raise SourceControlApiRuntimeError(
            "source_control_project_scope_mismatch",
            status_code=403,
        )


def _validate_grant_query(
    *, cursor: str | None, filters: Mapping[str, str]
) -> None:
    if cursor is not None and len(cursor) > 512:
        raise SourceControlApiRuntimeError("grant_cursor_invalid")
    for key, value in filters.items():
        maximum = 128 if key == "q" else 255
        if len(value) > maximum:
            raise SourceControlApiRuntimeError(
                f"grant_{key}_filter_invalid"
            )


def _encode_grant_preset_cursor(preset_id: str) -> str:
    return (
        base64.urlsafe_b64encode(preset_id.encode("ascii"))
        .decode("ascii")
        .rstrip("=")
    )


def _decode_grant_preset_cursor(cursor: str | None) -> str | None:
    if cursor in (None, ""):
        return None
    try:
        raw = str(cursor)
        raw += "=" * (-len(raw) % 4)
        preset_id = base64.urlsafe_b64decode(raw).decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceControlApiRuntimeError(
            "grant_preset_cursor_invalid"
        ) from exc
    if not preset_id or len(preset_id) > 255:
        raise SourceControlApiRuntimeError(
            "grant_preset_cursor_invalid"
        )
    return preset_id


def _grant_preset_capabilities(project_id: str) -> Mapping[str, object]:
    return {
        "read_only": True,
        "selection_mode": "server_ids_only",
        "project_id": project_id,
        "browser_ids_accepted": False,
        "create_grant": {
            "supported": True,
            "requires_if_match": True,
            "requires_idempotency_key": True,
        },
    }


def _grant_capabilities(project_id: str) -> Mapping[str, object]:
    return {
        "read_only": False,
        "selection_mode": "server_ids_only",
        "project_id": project_id,
        "browser_ids_accepted": False,
        "create_supported": True,
        "revoke_supported": True,
        "requires_if_match": True,
        "requires_idempotency_key": True,
        "destination_resolution": "server",
        "policy_resolution": "server",
    }


def _operation_key(
    namespace: str, tenant_id: str, idempotency_key: str
) -> str:
    digest = hashlib.sha256(
        f"{namespace}\0{tenant_id}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"{namespace}_{digest}"


def build_source_control_api_runtime(
    *,
    engine: Engine,
    access: object | None = None,
    operations: object | None = None,
    context_policy: object | None = None,
    artifact_deletion: ContainedArtifactDeletionService | None = None,
    content_admission: object | None = None,
    catalogs: object | None = None,
    grants: object | None = None,
    connection_intents: object | None = None,
    codehug_mutations: object | None = None,
    artifact_downloads: object | None = None,
) -> SourceControlApiRuntime:
    reads = SQLSourceControlReadRepository(engine)
    repository = SQLSourceIndexLifecycleRepository(
        engine,
        artifact_deletion=artifact_deletion,
    )
    purge_approvals = SQLSourceControlPurgeApprovalStore(engine)
    return SourceControlApiRuntime(
        engine=engine,
        reads=reads,
        projection=SourceControlProjectionService(reads),
        lifecycle=SourceIndexLifecycleService(
            repository=repository,
            audit=_LifecycleAudit(),
            approvals=purge_approvals,
            artifacts=artifact_deletion,
        ),
        events=SourceControlJobEventService(
            SQLSourceControlJobEventRepository(engine)
        ),
        idempotency=SQLSourceControlOperationStore(engine),
        access=access,
        operations=operations,
        context_policy=context_policy,
        artifact_deletion=artifact_deletion,
        content_admission=content_admission,
        catalogs=catalogs,
        grants=grants,
        connection_intents=connection_intents,
        codehug_mutations=codehug_mutations,
        artifact_downloads=artifact_downloads,
    )


__all__ = [
    "SQLSourceControlJobEventRepository",
    "SQLSourceControlOperationStore",
    "SQLSourceControlReadRepository",
    "SQLSourceIndexLifecycleRepository",
    "SourceControlApiRuntime",
    "SourceControlApiRuntimeError",
    "build_source_control_api_runtime",
]
