"""PostgreSQL adapter for shared tenant-scoped collaboration coordination."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from agent.services.collaboration_workspace_store import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import require_id

_METADATA = sa.MetaData()
_CURSORS = sa.Table(
    "collaboration_shared_cursors",
    _METADATA,
    sa.Column("tenant_id", sa.String),
    sa.Column("workspace_id", sa.String),
    sa.Column("room_id", sa.String),
    sa.Column("actor_binding_id", sa.String),
    sa.Column("view_id", sa.String),
    sa.Column("epoch", sa.BigInteger),
    sa.Column("expires_at", sa.Float),
    sa.Column("payload_json", sa.JSON),
)
_GRANTS = sa.Table(
    "collaboration_shared_control_grants",
    _METADATA,
    sa.Column("tenant_id", sa.String),
    sa.Column("workspace_id", sa.String),
    sa.Column("controlled_actor_binding_id", sa.String),
    sa.Column("controller_actor_binding_id", sa.String),
    sa.Column("revision", sa.BigInteger),
    sa.Column("expires_at", sa.Float),
    sa.Column("payload_json", sa.JSON),
)
_PRESENCE = sa.Table(
    "collaboration_shared_presence",
    _METADATA,
    sa.Column("tenant_id", sa.String),
    sa.Column("workspace_id", sa.String),
    sa.Column("actor_binding_id", sa.String),
    sa.Column("lease_id", sa.String),
    sa.Column("epoch", sa.BigInteger),
    sa.Column("expires_at", sa.Float),
    sa.Column("payload_json", sa.JSON),
)
_CACHE = sa.Table(
    "collaboration_shared_cache",
    _METADATA,
    sa.Column("tenant_id", sa.String),
    sa.Column("workspace_id", sa.String),
    sa.Column("namespace", sa.String),
    sa.Column("cache_key", sa.String),
    sa.Column("revision", sa.BigInteger),
    sa.Column("expires_at", sa.Float),
    sa.Column("payload_json", sa.JSON),
)


class PostgresCollaborationCoordinationRepository:
    """Shared live/presence/cache state with tenant-qualified database CAS."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("collaboration_shared_coordination_postgresql_required")
        self._engine = engine

    @classmethod
    def from_url(cls, database_url: str):
        return cls(sa.create_engine(str(database_url), pool_pre_ping=True))

    def put_cursor(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        actor_binding_id: str,
        cursor: Mapping[str, Any],
    ) -> dict[str, Any]:
        tenant, workspace = _workspace_scope(tenant_id, workspace_id)
        room = require_id(room_id, "room_id")
        actor = require_id(actor_binding_id, "actor_binding_id")
        value = dict(cursor)
        statement = (
            pg_insert(_CURSORS)
            .values(
                tenant_id=tenant,
                workspace_id=workspace,
                room_id=room,
                actor_binding_id=actor,
                view_id=require_id(value.get("view_id"), "view_id"),
                epoch=_positive_int(value.get("epoch"), "cursor_epoch"),
                expires_at=float(value["expires_at"]),
                payload_json=value,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "workspace_id", "room_id", "actor_binding_id"],
                set_={
                    "view_id": require_id(value.get("view_id"), "view_id"),
                    "epoch": _positive_int(value.get("epoch"), "cursor_epoch"),
                    "expires_at": float(value["expires_at"]),
                    "payload_json": value,
                },
                where=_CURSORS.c.epoch <= _positive_int(value.get("epoch"), "cursor_epoch"),
            )
        )
        with self._engine.begin() as connection:
            result = connection.execute(statement)
        if result.rowcount != 1:
            raise CollaborationStoreConflict("collaboration_cursor_epoch_stale")
        return value

    def cursors(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        view_id: str,
        now: float,
    ) -> list[dict[str, Any]]:
        tenant, workspace = _workspace_scope(tenant_id, workspace_id)
        with self._engine.connect() as connection:
            values = connection.execute(
                sa.select(_CURSORS.c.payload_json)
                .where(
                    _CURSORS.c.tenant_id == tenant,
                    _CURSORS.c.workspace_id == workspace,
                    _CURSORS.c.room_id == require_id(room_id, "room_id"),
                    _CURSORS.c.view_id == require_id(view_id, "view_id"),
                    _CURSORS.c.expires_at > float(now),
                )
                .order_by(_CURSORS.c.actor_binding_id)
            ).scalars()
            return [dict(value) for value in values]

    def compare_and_set_grant(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        controlled_actor_binding_id: str,
        expected_revision: int,
        grant: Mapping[str, Any],
        now: float,
    ) -> dict[str, Any]:
        tenant, workspace = _workspace_scope(tenant_id, workspace_id)
        controlled = require_id(controlled_actor_binding_id, "controlled_actor_binding_id")
        value = dict(grant)
        with self._engine.begin() as connection:
            _advisory_lock(connection, tenant, workspace, "control", controlled)
            current = connection.execute(
                sa.select(_GRANTS.c.revision, _GRANTS.c.expires_at).where(
                    _GRANTS.c.tenant_id == tenant,
                    _GRANTS.c.workspace_id == workspace,
                    _GRANTS.c.controlled_actor_binding_id == controlled,
                )
            ).first()
            if current is not None and float(current.expires_at) <= float(now):
                connection.execute(
                    sa.delete(_GRANTS).where(
                        _GRANTS.c.tenant_id == tenant,
                        _GRANTS.c.workspace_id == workspace,
                        _GRANTS.c.controlled_actor_binding_id == controlled,
                    )
                )
                current = None
            if int(current.revision if current is not None else 0) != expected_revision:
                raise CollaborationStoreConflict("collaboration_control_revision_conflict")
            connection.execute(
                pg_insert(_GRANTS)
                .values(
                    tenant_id=tenant,
                    workspace_id=workspace,
                    controlled_actor_binding_id=controlled,
                    controller_actor_binding_id=require_id(
                        value.get("controller_actor_binding_id"), "controller_actor_binding_id"
                    ),
                    revision=_positive_int(value.get("revision"), "control_revision"),
                    expires_at=float(value["expires_at"]),
                    payload_json=value,
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "workspace_id", "controlled_actor_binding_id"],
                    set_={
                        "controller_actor_binding_id": value["controller_actor_binding_id"],
                        "revision": value["revision"],
                        "expires_at": value["expires_at"],
                        "payload_json": value,
                    },
                )
            )
        return value

    def grants_for_actor(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_binding_id: str,
        now: float,
    ) -> list[dict[str, Any]]:
        tenant, workspace = _workspace_scope(tenant_id, workspace_id)
        actor = require_id(actor_binding_id, "actor_binding_id")
        with self._engine.connect() as connection:
            values = connection.execute(
                sa.select(_GRANTS.c.payload_json)
                .where(
                    _GRANTS.c.tenant_id == tenant,
                    _GRANTS.c.workspace_id == workspace,
                    _GRANTS.c.expires_at > float(now),
                    sa.or_(
                        _GRANTS.c.controller_actor_binding_id == actor,
                        _GRANTS.c.controlled_actor_binding_id == actor,
                    ),
                )
                .order_by(_GRANTS.c.controlled_actor_binding_id)
            ).scalars()
            return [dict(value) for value in values]

    def delete_grant(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        controlled_actor_binding_id: str,
        expected_revision: int,
    ) -> bool:
        tenant, workspace = _workspace_scope(tenant_id, workspace_id)
        controlled = require_id(controlled_actor_binding_id, "controlled_actor_binding_id")
        with self._engine.begin() as connection:
            _advisory_lock(connection, tenant, workspace, "control", controlled)
            result = connection.execute(
                sa.delete(_GRANTS).where(
                    _GRANTS.c.tenant_id == tenant,
                    _GRANTS.c.workspace_id == workspace,
                    _GRANTS.c.controlled_actor_binding_id == controlled,
                    _GRANTS.c.revision == expected_revision,
                )
            )
            if result.rowcount == 1:
                return True
            current = connection.execute(
                sa.select(_GRANTS.c.revision).where(
                    _GRANTS.c.tenant_id == tenant,
                    _GRANTS.c.workspace_id == workspace,
                    _GRANTS.c.controlled_actor_binding_id == controlled,
                )
            ).scalar_one_or_none()
            if current is not None:
                raise CollaborationStoreConflict("collaboration_control_revision_conflict")
            return False

    def renew_presence(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_binding_id: str,
        lease_id: str,
        epoch: int,
        expires_at: float,
    ) -> dict[str, Any]:
        tenant, workspace = _workspace_scope(tenant_id, workspace_id)
        actor = require_id(actor_binding_id, "actor_binding_id")
        value = {
            "actor_binding_id": actor,
            "lease_id": require_id(lease_id, "lease_id"),
            "epoch": _positive_int(epoch, "presence_epoch"),
            "expires_at": float(expires_at),
        }
        statement = (
            pg_insert(_PRESENCE)
            .values(tenant_id=tenant, workspace_id=workspace, payload_json=value, **value)
            .on_conflict_do_update(
                index_elements=["tenant_id", "workspace_id", "actor_binding_id"],
                set_={
                    "lease_id": value["lease_id"],
                    "epoch": value["epoch"],
                    "expires_at": value["expires_at"],
                    "payload_json": value,
                },
                where=_PRESENCE.c.epoch <= value["epoch"],
            )
        )
        with self._engine.begin() as connection:
            result = connection.execute(statement)
        if result.rowcount != 1:
            raise CollaborationStoreConflict("collaboration_presence_epoch_stale")
        return value

    def presence_values(
        self, *, tenant_id: str, workspace_id: str, now: float
    ) -> list[dict[str, Any]]:
        tenant, workspace = _workspace_scope(tenant_id, workspace_id)
        with self._engine.connect() as connection:
            values = connection.execute(
                sa.select(_PRESENCE.c.payload_json)
                .where(
                    _PRESENCE.c.tenant_id == tenant,
                    _PRESENCE.c.workspace_id == workspace,
                    _PRESENCE.c.expires_at > float(now),
                )
                .order_by(_PRESENCE.c.actor_binding_id)
            ).scalars()
            return [dict(value) for value in values]

    def compare_and_set_cache(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        namespace: str,
        cache_key: str,
        expected_revision: int,
        expires_at: float,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        tenant, workspace = _workspace_scope(tenant_id, workspace_id)
        namespace_id = require_id(namespace, "cache_namespace")
        key = require_id(cache_key, "cache_key")
        with self._engine.begin() as connection:
            _advisory_lock(connection, tenant, workspace, namespace_id, key)
            current = connection.execute(
                sa.select(_CACHE.c.revision).where(
                    _CACHE.c.tenant_id == tenant,
                    _CACHE.c.workspace_id == workspace,
                    _CACHE.c.namespace == namespace_id,
                    _CACHE.c.cache_key == key,
                )
            ).scalar_one_or_none()
            if int(current or 0) != expected_revision:
                raise CollaborationStoreConflict("collaboration_shared_cache_revision_conflict")
            revision = expected_revision + 1
            value = {
                "namespace": namespace_id,
                "cache_key": key,
                "revision": revision,
                "expires_at": float(expires_at),
                "payload": dict(payload),
            }
            connection.execute(
                pg_insert(_CACHE)
                .values(
                    tenant_id=tenant,
                    workspace_id=workspace,
                    namespace=namespace_id,
                    cache_key=key,
                    revision=revision,
                    expires_at=float(expires_at),
                    payload_json=value,
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "workspace_id", "namespace", "cache_key"],
                    set_={"revision": revision, "expires_at": float(expires_at), "payload_json": value},
                )
            )
        return value

    def cache_value(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        namespace: str,
        cache_key: str,
        now: float,
    ) -> dict[str, Any] | None:
        tenant, workspace = _workspace_scope(tenant_id, workspace_id)
        with self._engine.connect() as connection:
            value = connection.execute(
                sa.select(_CACHE.c.payload_json).where(
                    _CACHE.c.tenant_id == tenant,
                    _CACHE.c.workspace_id == workspace,
                    _CACHE.c.namespace == require_id(namespace, "cache_namespace"),
                    _CACHE.c.cache_key == require_id(cache_key, "cache_key"),
                    _CACHE.c.expires_at > float(now),
                )
            ).scalar_one_or_none()
        return dict(value) if value is not None else None


def _workspace_scope(tenant_id: object, workspace_id: object) -> tuple[str, str]:
    return require_id(tenant_id, "tenant_id"), require_id(workspace_id, "workspace_id")


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"collaboration_{field}_invalid")
    return value


def _advisory_lock(connection, *parts: str) -> None:
    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": "\x1f".join(parts)},
    )


__all__ = ["PostgresCollaborationCoordinationRepository"]
