"""Hub-authorized ephemeral cursors, follow context and remote-control grants."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from agent.services.collaboration_live_state_repository import (
    CollaborationLiveStateRepository,
    InMemoryCollaborationLiveStateRepository,
)
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationStoreConflict, CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import require_id


class CollaborationLiveControlService:
    """Keeps ephemeral state separate while revalidating durable Hub authority."""

    def __init__(
        self,
        store: CollaborationWorkspaceStore,
        *,
        policy: CollaborationWorkspacePolicy,
        state: CollaborationLiveStateRepository | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._policy = policy
        self._state = state or InMemoryCollaborationLiveStateRepository()
        self._clock = clock

    def publish_cursor(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        principal_actor_id: str,
        view_id: str,
        x: float,
        y: float,
        epoch: int,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, room_id, principal_actor_id, "cursor.write")
        if (
            not isinstance(epoch, int)
            or isinstance(epoch, bool)
            or epoch < 1
            or not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or not 1 <= ttl_seconds <= 30
            or not isinstance(x, (int, float))
            or isinstance(x, bool)
            or not math.isfinite(float(x))
            or not isinstance(y, (int, float))
            or isinstance(y, bool)
            or not math.isfinite(float(y))
            or not 0 <= float(x) <= 1
            or not 0 <= float(y) <= 1
        ):
            raise ValueError("collaboration_cursor_state_invalid")
        keys = self._keys(tenant_id, workspace_id, room_id)
        actor = require_id(principal_actor_id, "actor_binding_id")
        view = require_id(view_id, "view_id")
        cursor = {
            "cursor_id": f"cursor-{actor}",
            "actor_binding_id": actor,
            "room_id": keys[2],
            "view_id": view,
            "x": float(x),
            "y": float(y),
            "epoch": epoch,
            "expires_at": self._clock() + ttl_seconds,
        }
        return self._state.put_cursor(
            tenant_id=keys[0],
            workspace_id=keys[1],
            room_id=keys[2],
            actor_binding_id=actor,
            cursor=cursor,
        )

    def cursors(
        self, *, tenant_id: str, workspace_id: str, room_id: str, principal_actor_id: str, view_id: str
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, room_id, principal_actor_id, "event.read")
        keys = self._keys(tenant_id, workspace_id, room_id)
        view = require_id(view_id, "view_id")
        now = self._clock()
        items = self._state.cursors(
            tenant_id=keys[0], workspace_id=keys[1], room_id=keys[2], view_id=view, now=now
        )
        return {"items": items, "server_time": now}

    def grant_control(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        principal_actor_id: str,
        controller_actor_binding_id: str,
        session_id: str,
        view_id: str,
        epoch: int,
        expected_revision: int,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, room_id, principal_actor_id, "cursor.write")
        controller = require_id(controller_actor_binding_id, "controller_actor_binding_id")
        self._authorize(tenant_id, workspace_id, room_id, controller, "cursor.write")
        if controller == principal_actor_id:
            raise ValueError("collaboration_control_self_grant_invalid")
        if (
            not isinstance(epoch, int)
            or isinstance(epoch, bool)
            or epoch < 1
            or not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
            or not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or not 1 <= ttl_seconds <= 300
        ):
            raise ValueError("collaboration_control_grant_invalid")
        keys = self._keys(tenant_id, workspace_id, room_id)
        session = require_id(session_id, "session_id")
        controlled = require_id(principal_actor_id, "controlled_actor_binding_id")
        membership = self._store.membership(tenant_id, workspace_id, controlled) or {}
        revision = expected_revision + 1
        now = self._clock()
        grant = {
            "grant_id": f"control-{controlled}-{revision}",
            "session_id": session,
            "room_id": keys[2],
            "view_id": require_id(view_id, "view_id"),
            "controller_actor_binding_id": controller,
            "controlled_actor_binding_id": controlled,
            "revision": revision,
            "epoch": epoch,
            "controlled_membership_revision": int(membership.get("revision") or 0),
            "expires_at": now + ttl_seconds,
        }
        return self._state.compare_and_set_grant(
            tenant_id=keys[0],
            workspace_id=keys[1],
            controlled_actor_binding_id=controlled,
            expected_revision=expected_revision,
            grant=grant,
            now=now,
        )

    def current_grant(self, *, tenant_id: str, workspace_id: str, principal_actor_id: str) -> dict[str, Any] | None:
        actor = require_id(principal_actor_id, "actor_binding_id")
        candidates = self._state.grants_for_actor(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_binding_id=actor,
            now=self._clock(),
        )
        for grant in candidates:
            controlled = grant["controlled_actor_binding_id"]
            membership = self._store.membership(tenant_id, workspace_id, controlled)
            if (
                not membership
                or membership.get("status") != "active"
                or membership.get("revision") != grant["controlled_membership_revision"]
                or not self._store.room_visible(tenant_id, workspace_id, grant["room_id"], actor)
            ):
                self._state.delete_grant(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    controlled_actor_binding_id=controlled,
                    expected_revision=grant["revision"],
                )
                continue
            return dict(grant)
        return None

    def revoke_control(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        grant = self.current_grant(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_actor_id=principal_actor_id,
        )
        if grant is None:
            return {"revoked": False, "reason_code": "collaboration_control_grant_absent"}
        if grant["revision"] != expected_revision:
            raise CollaborationStoreConflict("collaboration_control_revision_conflict")
        self._state.delete_grant(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            controlled_actor_binding_id=grant["controlled_actor_binding_id"],
            expected_revision=expected_revision,
        )
        return {
            "revoked": True,
            "revision": expected_revision,
            "reason_code": "collaboration_control_revoked",
        }

    def _authorize(self, tenant_id: str, workspace_id: str, room_id: str, actor_id: str, capability: str) -> None:
        self._policy.require(self._store.membership(tenant_id, workspace_id, actor_id), capability)
        if not self._store.room_visible(tenant_id, workspace_id, room_id, actor_id):
            raise PermissionError("collaboration_room_visibility_denied")

    @staticmethod
    def _keys(tenant_id: str, workspace_id: str, room_id: str) -> tuple[str, str, str]:
        return (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(room_id, "room_id"),
        )

__all__ = ["CollaborationLiveControlService"]
