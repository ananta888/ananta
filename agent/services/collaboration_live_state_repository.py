"""Ports and local adapter for tenant-scoped ephemeral collaboration state."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.collaboration_workspace_store import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import require_id


class CollaborationLiveStateRepository(Protocol):
    """Small CAS/cache port used by Hub live-control services."""

    def put_cursor(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        actor_binding_id: str,
        cursor: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def cursors(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        view_id: str,
        now: float,
    ) -> list[dict[str, Any]]: ...

    def compare_and_set_grant(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        controlled_actor_binding_id: str,
        expected_revision: int,
        grant: Mapping[str, Any],
        now: float,
    ) -> dict[str, Any]: ...

    def grants_for_actor(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_binding_id: str,
        now: float,
    ) -> list[dict[str, Any]]: ...

    def delete_grant(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        controlled_actor_binding_id: str,
        expected_revision: int,
    ) -> bool: ...


class InMemoryCollaborationLiveStateRepository:
    """Single-process adapter with the same tenant-qualified CAS contract."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cursors: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._grants: dict[tuple[str, str, str], dict[str, Any]] = {}

    def put_cursor(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        actor_binding_id: str,
        cursor: Mapping[str, Any],
    ) -> dict[str, Any]:
        key = (*_room_scope(tenant_id, workspace_id, room_id), require_id(actor_binding_id, "actor_binding_id"))
        value = dict(cursor)
        with self._lock:
            self._cursors[key] = value
        return dict(value)

    def cursors(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        view_id: str,
        now: float,
    ) -> list[dict[str, Any]]:
        scope = _room_scope(tenant_id, workspace_id, room_id)
        view = require_id(view_id, "view_id")
        with self._lock:
            self._discard_expired(now)
            values = [
                dict(cursor)
                for key, cursor in self._cursors.items()
                if key[:3] == scope and cursor["view_id"] == view
            ]
        return sorted(values, key=lambda item: item["actor_binding_id"])

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
        key = _grant_key(tenant_id, workspace_id, controlled_actor_binding_id)
        with self._lock:
            self._discard_expired(now)
            current_revision = int((self._grants.get(key) or {}).get("revision") or 0)
            if current_revision != expected_revision:
                raise CollaborationStoreConflict("collaboration_control_revision_conflict")
            value = dict(grant)
            self._grants[key] = value
        return dict(value)

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
        with self._lock:
            self._discard_expired(now)
            return [
                dict(grant)
                for key, grant in self._grants.items()
                if key[:2] == (tenant, workspace)
                and actor in {grant["controller_actor_binding_id"], grant["controlled_actor_binding_id"]}
            ]

    def delete_grant(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        controlled_actor_binding_id: str,
        expected_revision: int,
    ) -> bool:
        key = _grant_key(tenant_id, workspace_id, controlled_actor_binding_id)
        with self._lock:
            current = self._grants.get(key)
            if current is None:
                return False
            if int(current["revision"]) != expected_revision:
                raise CollaborationStoreConflict("collaboration_control_revision_conflict")
            self._grants.pop(key)
        return True

    def _discard_expired(self, now: float) -> None:
        self._cursors = {key: value for key, value in self._cursors.items() if value["expires_at"] > now}
        self._grants = {key: value for key, value in self._grants.items() if value["expires_at"] > now}


def _workspace_scope(tenant_id: object, workspace_id: object) -> tuple[str, str]:
    return require_id(tenant_id, "tenant_id"), require_id(workspace_id, "workspace_id")


def _room_scope(tenant_id: object, workspace_id: object, room_id: object) -> tuple[str, str, str]:
    tenant, workspace = _workspace_scope(tenant_id, workspace_id)
    return tenant, workspace, require_id(room_id, "room_id")


def _grant_key(tenant_id: object, workspace_id: object, actor_id: object) -> tuple[str, str, str]:
    tenant, workspace = _workspace_scope(tenant_id, workspace_id)
    return tenant, workspace, require_id(actor_id, "controlled_actor_binding_id")


__all__ = ["CollaborationLiveStateRepository", "InMemoryCollaborationLiveStateRepository"]
