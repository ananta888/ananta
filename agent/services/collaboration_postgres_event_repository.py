"""PostgreSQL repository for shared append-only collaboration streams."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from agent.services.collaboration_workspace_store import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import require_digest, require_id

_METADATA = sa.MetaData()
_STREAMS = sa.Table(
    "collaboration_event_streams",
    _METADATA,
    sa.Column("tenant_id", sa.String),
    sa.Column("workspace_id", sa.String),
    sa.Column("next_sequence", sa.BigInteger),
)
_EVENTS = sa.Table(
    "collaboration_durable_events",
    _METADATA,
    sa.Column("tenant_id", sa.String),
    sa.Column("workspace_id", sa.String),
    sa.Column("sequence", sa.BigInteger),
    sa.Column("event_id", sa.String),
    sa.Column("idempotency_key", sa.String),
    sa.Column("room_id", sa.String),
    sa.Column("event_type", sa.String),
    sa.Column("payload_digest", sa.String),
    sa.Column("admitted_at", sa.Float),
    sa.Column("payload_json", sa.JSON),
)
_OUTBOX = sa.Table(
    "collaboration_shared_outbox",
    _METADATA,
    sa.Column("tenant_id", sa.String),
    sa.Column("workspace_id", sa.String),
    sa.Column("event_id", sa.String),
    sa.Column("sequence", sa.BigInteger),
    sa.Column("topic", sa.String),
    sa.Column("status", sa.String),
    sa.Column("payload_json", sa.JSON),
)
_CHECKPOINTS = sa.Table(
    "collaboration_shared_projection_checkpoints",
    _METADATA,
    sa.Column("tenant_id", sa.String),
    sa.Column("workspace_id", sa.String),
    sa.Column("projection_name", sa.String),
    sa.Column("checkpoint", sa.BigInteger),
    sa.Column("revision", sa.BigInteger),
    sa.Column("state_digest", sa.String),
)


class PostgresCollaborationEventRepository:
    """Serializes each workspace stream with a database row lock."""

    def __init__(self, engine: Engine, *, clock: Callable[[], float] = time.time) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("collaboration_shared_event_store_postgresql_required")
        self._engine = engine
        self._clock = clock

    @classmethod
    def from_url(cls, database_url: str, *, clock: Callable[[], float] = time.time):
        engine = sa.create_engine(str(database_url), pool_pre_ping=True)
        return cls(engine, clock=clock)

    def admit_workspace(self, *, tenant_id: str, workspace_id: str) -> None:
        tenant, workspace = _scope(tenant_id, workspace_id)
        with self._engine.begin() as connection:
            connection.execute(
                pg_insert(_STREAMS)
                .values(tenant_id=tenant, workspace_id=workspace, next_sequence=1)
                .on_conflict_do_nothing(index_elements=["tenant_id", "workspace_id"])
            )

    def append(self, *, tenant_id: str, event: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        tenant, workspace = _scope(tenant_id, event.get("workspace_id"))
        event_id = require_id(event.get("event_id"), "event_id")
        idempotency_key = require_id(event.get("idempotency_key"), "idempotency_key")
        digest = require_digest(event.get("payload_digest"), "payload_digest")
        room_id = require_id(event.get("room_id"), "room_id") if event.get("room_id") is not None else None
        event_type = require_id(event.get("event_type"), "event_type")
        with self._engine.begin() as connection:
            stream = connection.execute(
                sa.select(_STREAMS.c.next_sequence)
                .where(_STREAMS.c.tenant_id == tenant, _STREAMS.c.workspace_id == workspace)
                .with_for_update()
            ).first()
            if stream is None:
                raise KeyError("collaboration_workspace_stream_not_admitted")
            existing = connection.execute(
                sa.select(_EVENTS.c.payload_json).where(
                    _EVENTS.c.tenant_id == tenant,
                    _EVENTS.c.workspace_id == workspace,
                    _EVENTS.c.idempotency_key == idempotency_key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing["event_id"] != event_id or existing["payload_digest"] != digest:
                    raise CollaborationStoreConflict("collaboration_event_idempotency_conflict")
                return dict(existing), True
            sequence = int(stream.next_sequence)
            admitted = {
                **dict(event),
                "tenant_id": tenant,
                "workspace_id": workspace,
                "sequence": sequence,
                "admitted_at": float(self._clock()),
            }
            connection.execute(
                sa.insert(_EVENTS).values(
                    tenant_id=tenant,
                    workspace_id=workspace,
                    sequence=sequence,
                    event_id=event_id,
                    idempotency_key=idempotency_key,
                    room_id=room_id,
                    event_type=event_type,
                    payload_digest=digest,
                    admitted_at=admitted["admitted_at"],
                    payload_json=admitted,
                )
            )
            connection.execute(
                sa.update(_STREAMS)
                .where(_STREAMS.c.tenant_id == tenant, _STREAMS.c.workspace_id == workspace)
                .values(next_sequence=sequence + 1)
            )
            connection.execute(
                sa.insert(_OUTBOX).values(
                    tenant_id=tenant,
                    workspace_id=workspace,
                    event_id=event_id,
                    sequence=sequence,
                    topic="collaboration.workspace-event.v1",
                    status="pending",
                    payload_json=admitted,
                )
            )
        return admitted, False

    def events(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str | None = None,
        after: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        tenant, workspace = _scope(tenant_id, workspace_id)
        if after < 0 or not 1 <= limit <= 1000:
            raise ValueError("collaboration_shared_event_page_invalid")
        query = sa.select(_EVENTS.c.payload_json).where(
            _EVENTS.c.tenant_id == tenant,
            _EVENTS.c.workspace_id == workspace,
            _EVENTS.c.sequence > after,
        )
        if room_id is not None:
            query = query.where(_EVENTS.c.room_id == require_id(room_id, "room_id"))
        query = query.order_by(_EVENTS.c.sequence).limit(limit)
        with self._engine.connect() as connection:
            return [dict(value) for value in connection.execute(query).scalars()]

    def advance_checkpoint(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        projection_name: str,
        checkpoint: int,
        state_digest: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        tenant, workspace = _scope(tenant_id, workspace_id)
        projection = require_id(projection_name, "projection_name")
        digest = require_digest(state_digest, "state_digest")
        if (
            not isinstance(checkpoint, int)
            or isinstance(checkpoint, bool)
            or checkpoint < 0
            or not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            raise ValueError("collaboration_projection_checkpoint_invalid")
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(_CHECKPOINTS)
                    .where(
                        _CHECKPOINTS.c.tenant_id == tenant,
                        _CHECKPOINTS.c.workspace_id == workspace,
                        _CHECKPOINTS.c.projection_name == projection,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            actual_revision = int(row["revision"]) if row else 0
            current_checkpoint = int(row["checkpoint"]) if row else 0
            if actual_revision != expected_revision:
                raise CollaborationStoreConflict("collaboration_projection_checkpoint_revision_conflict")
            if checkpoint < current_checkpoint:
                raise CollaborationStoreConflict("collaboration_projection_checkpoint_regression")
            maximum = connection.execute(
                sa.select(sa.func.coalesce(sa.func.max(_EVENTS.c.sequence), 0)).where(
                    _EVENTS.c.tenant_id == tenant,
                    _EVENTS.c.workspace_id == workspace,
                )
            ).scalar_one()
            if checkpoint > int(maximum):
                raise CollaborationStoreConflict("collaboration_projection_checkpoint_ahead")
            revision = actual_revision + 1
            connection.execute(
                pg_insert(_CHECKPOINTS)
                .values(
                    tenant_id=tenant,
                    workspace_id=workspace,
                    projection_name=projection,
                    checkpoint=checkpoint,
                    revision=revision,
                    state_digest=digest,
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "workspace_id", "projection_name"],
                    set_={"checkpoint": checkpoint, "revision": revision, "state_digest": digest},
                )
            )
        return {
            "tenant_id": tenant,
            "workspace_id": workspace,
            "projection_name": projection,
            "checkpoint": checkpoint,
            "revision": revision,
            "state_digest": digest,
        }

    def pending_outbox(self, *, tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("collaboration_shared_outbox_limit_invalid")
        tenant = require_id(tenant_id, "tenant_id")
        with self._engine.connect() as connection:
            rows = connection.execute(
                sa.select(_OUTBOX.c.payload_json)
                .where(_OUTBOX.c.tenant_id == tenant, _OUTBOX.c.status == "pending")
                .order_by(_OUTBOX.c.workspace_id, _OUTBOX.c.sequence)
                .limit(limit)
            ).scalars()
            return [dict(value) for value in rows]


def _scope(tenant_id: object, workspace_id: object) -> tuple[str, str]:
    return require_id(tenant_id, "tenant_id"), require_id(workspace_id, "workspace_id")


__all__ = ["PostgresCollaborationEventRepository"]
