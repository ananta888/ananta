"""Hub application service for durable native collaboration workspaces."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from typing import Any

from agent.services.collaboration_budget_service import CollaborationBudgetService
from agent.services.collaboration_event_policy import CollaborationEventPolicy
from agent.services.collaboration_evidence_policy import CollaborationEvidencePolicy
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import (
    CollaborationRoomV1,
    CollaborationWorkspaceV1,
    WorkspaceActorBindingV1,
    WorkspaceEventV1,
    canonical_digest,
    require_id,
)


class CollaborationWorkspaceService:
    def __init__(
        self,
        store: CollaborationWorkspaceStore,
        *,
        policy: CollaborationWorkspacePolicy,
        event_policy: CollaborationEventPolicy | None = None,
        evidence_policy: CollaborationEvidencePolicy | None = None,
        budget: CollaborationBudgetService | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._event_policy = event_policy or CollaborationEventPolicy()
        self._evidence_policy = evidence_policy or CollaborationEvidencePolicy(None)
        self._budget = budget

    def create_workspace(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        title: str,
        owner: Mapping[str, Any],
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        actor = WorkspaceActorBindingV1.from_mapping(owner)
        if actor.authority_subject != principal_id or actor.actor_kind != "human":
            raise PermissionError("collaboration_owner_principal_binding_invalid")
        normalized_title = str(title or "").strip()
        if not 1 <= len(normalized_title) <= 200:
            raise ValueError("collaboration_workspace_title_invalid")
        workspace = CollaborationWorkspaceV1.from_mapping(
            {
                "schema": "ananta.collaboration-workspace.v1",
                "tenant_id": require_id(tenant_id, "tenant_id"),
                "workspace_id": require_id(workspace_id or f"workspace-{uuid.uuid4()}", "workspace_id"),
                "project_id": None,
                "title": normalized_title,
                "state": "active",
                "retention": "standard",
                "revision": 1,
                "created_by": actor.actor_binding_id,
                "created_at": time.time(),
                "native_core": True,
                "bridge_required": False,
                "human_intervention_required": False,
            }
        ).to_dict()
        return self._store.create_workspace(workspace, actor.to_dict())

    def create_room(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        room: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "room.manage")
        parsed = CollaborationRoomV1.from_mapping(room)
        return self._store.add_room(tenant_id, workspace_id, parsed.to_dict())

    def reset_budget(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        dimension: str,
        subject: str,
        traffic_class: str,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "workspace.manage")
        if self._budget is None:
            raise RuntimeError("collaboration_budget_unavailable")
        return self._budget.reset_scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dimension=dimension,
            subject=subject,
            traffic_class=traffic_class,
        )

    def put_room_access(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        room_id: str,
        access_mode: str,
        actor_binding_ids: list[str],
        expected_revision: int,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "room.manage")
        allowed = sorted(set(actor_binding_ids) | {principal_actor_id}) if access_mode == "restricted" else []
        return self._store.put_room_access(
            tenant_id,
            workspace_id,
            room_id,
            access_mode=access_mode,
            actor_binding_ids=allowed,
            expected_revision=expected_revision,
        )

    def transition_room(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        room_id: str,
        target_state: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "room.manage")
        return self._store.transition_room(
            tenant_id,
            workspace_id,
            room_id,
            target_state=target_state,
            expected_revision=expected_revision,
        )

    def put_membership(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        actor: Mapping[str, Any],
        role: str,
        status: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "workspace.manage")
        parsed = WorkspaceActorBindingV1.from_mapping(actor)
        if parsed.actor_kind == "worker" and parsed.authority_kind != "registered_worker":
            raise PermissionError("collaboration_worker_registration_required")
        return self._store.put_membership(
            tenant_id,
            workspace_id,
            parsed.to_dict(),
            role=role,
            status=status,
            expected_revision=expected_revision,
        )

    def put_external_identity(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        actor_binding_id: str,
        provider: str,
        external_subject: str,
        key_fingerprint: str,
        status: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "workspace.manage")
        membership = self._store.membership(tenant_id, workspace_id, actor_binding_id)
        if membership is None:
            raise KeyError("collaboration_actor_membership_not_found")
        return self._store.put_external_identity(
            tenant_id,
            actor_binding_id=actor_binding_id,
            provider=provider,
            external_subject=external_subject,
            key_fingerprint=key_fingerprint,
            status=status,
            expected_revision=expected_revision,
        )

    def append_event(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        event: Mapping[str, Any],
        connection_id: str | None = None,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "event.write")
        parsed = WorkspaceEventV1.from_mapping(event)
        if parsed.workspace_id != workspace_id or parsed.actor_binding_id != principal_actor_id:
            raise PermissionError("collaboration_event_binding_invalid")
        self._event_policy.require_durable(parsed.event_type, parsed.payload)
        if self._budget is not None:
            budget = self._budget.admit(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                traffic_class=self._event_policy.classify(parsed.event_type).traffic_class,
                dimensions={
                    "room": parsed.room_id,
                    "principal": principal_actor_id,
                    "actor": parsed.actor_binding_id,
                    "task": str(parsed.payload.get("task_id") or "").strip() or None,
                    "provider": str(parsed.payload.get("provider") or "").strip() or None,
                    "intent_chain": parsed.correlation_id,
                    "connection": connection_id,
                },
            )
            if not budget["allowed"]:
                raise PermissionError(budget["reason_code"])
        self._evidence_policy.require_verified(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            event_type=parsed.event_type,
            payload=parsed.payload,
            source_refs=parsed.source_refs,
            run_refs=parsed.run_refs,
        )
        if parsed.room_id is not None and not self._store.room_visible(
            tenant_id, workspace_id, parsed.room_id, principal_actor_id
        ):
            raise PermissionError("collaboration_room_visibility_denied")
        value, replayed = self._store.append_event(tenant_id, parsed.to_dict())
        return {**value, "replayed": replayed, "human_intervention_required": False}

    def timeline(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        room_id: str | None = None,
        after: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "event.read")
        if room_id is not None and not self._store.room_visible(tenant_id, workspace_id, room_id, principal_actor_id):
            raise PermissionError("collaboration_room_visibility_denied")
        return self._store.timeline(
            tenant_id,
            workspace_id,
            actor_binding_id=principal_actor_id,
            room_id=room_id,
            after=after,
            limit=limit,
        )

    def search(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        query: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "event.read")
        return self._store.search(tenant_id, workspace_id, principal_actor_id, query, limit=limit)

    def query_events(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        filters: Mapping[str, Any],
        limit: int = 100,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "event.read")
        return self._store.query_events(
            tenant_id,
            workspace_id,
            actor_binding_id=principal_actor_id,
            filters=filters,
            limit=limit,
        )

    def acknowledge(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        room_id: str,
        sequence: int,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "cursor.write")
        if not self._store.room_visible(tenant_id, workspace_id, room_id, principal_actor_id):
            raise PermissionError("collaboration_room_visibility_denied")
        return self._store.acknowledge(tenant_id, workspace_id, room_id, principal_actor_id, sequence)

    def thread(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        thread_id: str,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "event.read")
        return self._store.thread(
            tenant_id,
            workspace_id,
            actor_binding_id=principal_actor_id,
            thread_id=thread_id,
        )

    def renew_presence(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        lease_id: str,
        ttl_seconds: int,
        epoch: int,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "presence.write")
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 5 <= ttl_seconds <= 300:
            raise ValueError("collaboration_presence_ttl_invalid")
        return self._store.renew_presence(
            tenant_id,
            workspace_id,
            principal_actor_id,
            lease_id,
            time.time() + ttl_seconds,
            epoch,
        )

    def room_presence(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        principal_actor_id: str,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "event.read")
        return {
            "items": self._store.room_presence(
                tenant_id,
                workspace_id,
                room_id,
                principal_actor_id,
                now=time.time(),
            ),
            "room_id": room_id,
        }

    def get_workspace(self, *, tenant_id: str, workspace_id: str, principal_actor_id: str) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "event.read")
        workspace = self._store.get_workspace(tenant_id, workspace_id)
        workspace["rooms"] = [
            room
            for room in workspace["rooms"]
            if self._store.room_visible(tenant_id, workspace_id, room["room_id"], principal_actor_id)
        ]
        return {**workspace, "current_actor_binding_id": principal_actor_id}

    def list_workspaces(self, *, tenant_id: str, principal_actor_id: str, limit: int = 100) -> dict[str, Any]:
        result = self._store.list_workspaces(tenant_id, principal_actor_id, limit=limit)
        return {
            **result,
            "items": [{**item, "current_actor_binding_id": principal_actor_id} for item in result["items"]],
        }

    def legacy_migration_plan(
        self,
        *,
        tenant_id: str,
        principal_actor_id: str,
        share_session: Mapping[str, Any],
    ) -> dict[str, Any]:
        session_id = require_id(share_session.get("id"), "share_session_id")
        if str(share_session.get("tenant_id") or "default") != tenant_id:
            raise PermissionError("collaboration_legacy_tenant_mismatch")
        if str(share_session.get("owner_user_id") or "") != principal_actor_id:
            raise PermissionError("collaboration_legacy_owner_required")
        mapping = {
            "workspace_id": f"legacy-workspace-{session_id}",
            "room_id": f"legacy-room-{session_id}",
            "room_kind": "pair_session",
            "source_revision": canonical_digest(dict(share_session)),
        }
        return {
            "schema": "ananta.collaboration-legacy-migration-plan.v1",
            "mode": "dry_run",
            "admissible": True,
            "mapping": mapping,
            "conflicts": [],
            "writes_performed": False,
            "human_intervention_required": False,
        }

    def _authorize(self, tenant_id: str, workspace_id: str, actor_id: str, capability: str) -> None:
        membership = self._store.membership(tenant_id, workspace_id, actor_id)
        self._policy.require(membership, capability)


def build_event(
    *,
    workspace_id: str,
    actor_binding_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    room_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Build a complete local event envelope without manufacturing evidence IDs."""

    event_payload = dict(payload)
    return {
        "schema": WorkspaceEventV1.SCHEMA,
        "event_id": f"event-{uuid.uuid4()}",
        "workspace_id": workspace_id,
        "room_id": room_id,
        "thread_id": thread_id,
        "event_type": event_type,
        "actor_binding_id": actor_binding_id,
        "idempotency_key": idempotency_key,
        "correlation_id": f"correlation-{uuid.uuid4()}",
        "causation_id": None,
        "visibility": "room" if room_id else "workspace",
        "retention": "standard",
        "occurred_at": time.time(),
        "payload": event_payload,
        "payload_digest": canonical_digest(event_payload),
        "source_refs": [],
        "run_refs": [],
    }


__all__ = ["CollaborationWorkspaceService", "build_event"]
