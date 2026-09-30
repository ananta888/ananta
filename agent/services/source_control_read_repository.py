"""Scoped SQL read model behind the Source Control projections, bindings and ETags.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceRevisionDB,
)
from agent.services.source_control_projection_service import (
    SourceControlAggregateRecord,
    SourceControlPage,
)
from agent.services.source_control_runtime_support import (
    SourceControlApiRuntimeError,
    decode_cursor,
    encode_cursor,
    iso_timestamp,
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
        after_id = decode_cursor(cursor)
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
                    encode_cursor(selected[-1].connection_id)
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
            key=lambda row: float(row.captured_at_epoch),
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
                    "admission_state": revision.admission_state,
                    "captured_at": iso_timestamp(revision.captured_at_epoch),
                }
                if revision is not None
                else None
            ),
            admission={
                "state": (
                    revision.admission_state
                    if revision is not None
                    else "pending"
                )
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


__all__ = ["SQLSourceControlReadRepository"]
