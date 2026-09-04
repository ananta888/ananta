"""Server-read, dry-run-first migration from legacy ShareSession state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.collaboration_workspace_service import CollaborationWorkspaceService, build_event
from agent.services.collaboration_workspace_store import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import canonical_digest, require_id


class LegacyShareSessionReadPort(Protocol):
    def get_session(self, session_id: str) -> Mapping[str, Any] | None: ...


class CollaborationLegacyMigrationService:
    def __init__(self, legacy: LegacyShareSessionReadPort, workspaces: CollaborationWorkspaceService) -> None:
        self._legacy = legacy
        self._workspaces = workspaces

    def plan(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        principal_actor_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        session = self._session(tenant_id, principal_id, session_id)
        revision = canonical_digest(session)
        workspace_id = f"legacy-workspace-{require_id(session_id, 'share_session_id')}"
        room_id = f"legacy-room-{session_id}"
        conflicts: list[str] = []
        try:
            existing = self._workspaces.get_workspace(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                principal_actor_id=principal_actor_id,
            )
        except (KeyError, PermissionError):
            existing = None
        if existing:
            room_ids = {room["room_id"] for room in existing.get("rooms") or []}
            if room_id in room_ids:
                timeline = self._workspaces.timeline(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    principal_actor_id=principal_actor_id,
                    room_id=room_id,
                    limit=200,
                )
                prior_revisions = {
                    (event.get("payload") or {}).get("source_revision")
                    for event in timeline["items"]
                    if event["event_type"] == "legacy.share_session.observed"
                }
                if prior_revisions and prior_revisions != {revision}:
                    conflicts.append("legacy_source_revision_changed")
        return {
            "schema": "ananta.collaboration-legacy-migration-plan.v1",
            "mode": "dry_run",
            "admissible": not conflicts,
            "mapping": {
                "workspace_id": workspace_id,
                "room_id": room_id,
                "room_kind": "pair_session",
                "source_revision": revision,
            },
            "conflicts": conflicts,
            "writes_performed": False,
            "human_intervention_required": False,
        }

    def execute(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        principal_actor_id: str,
        session_id: str,
        expected_source_revision: str,
        owner: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan = self.plan(
            tenant_id=tenant_id,
            principal_id=principal_id,
            principal_actor_id=principal_actor_id,
            session_id=session_id,
        )
        if not plan["admissible"] or plan["mapping"]["source_revision"] != expected_source_revision:
            raise CollaborationStoreConflict("collaboration_legacy_migration_revision_conflict")
        session = self._session(tenant_id, principal_id, session_id)
        mapping = plan["mapping"]
        created_workspace = True
        try:
            self._workspaces.create_workspace(
                tenant_id=tenant_id,
                principal_id=principal_id,
                title=str(session.get("title") or "Migrated Pair Session"),
                owner=owner,
                workspace_id=mapping["workspace_id"],
            )
        except CollaborationStoreConflict as exc:
            if str(exc) != "collaboration_workspace_exists":
                raise
            created_workspace = False
        created_room = True
        try:
            self._workspaces.create_room(
                tenant_id=tenant_id,
                workspace_id=mapping["workspace_id"],
                principal_actor_id=principal_actor_id,
                room={
                    "schema": "ananta.collaboration-room.v1",
                    "room_id": mapping["room_id"],
                    "room_kind": "pair_session",
                    "title": str(session.get("title") or "Migrated Pair Session"),
                    "binding_kind": "share_session",
                    "binding_id": session_id,
                },
            )
        except CollaborationStoreConflict as exc:
            if str(exc) != "collaboration_room_exists":
                raise
            created_room = False
        observed = build_event(
            workspace_id=mapping["workspace_id"],
            room_id=mapping["room_id"],
            actor_binding_id=principal_actor_id,
            event_type="legacy.share_session.observed",
            payload={
                "legacy_session_id": session_id,
                "source_revision": expected_source_revision,
                "authority_switched": False,
            },
            idempotency_key=f"legacy-observed-{expected_source_revision[:32]}",
        )
        observed["event_id"] = f"event-legacy-{expected_source_revision[:32]}"
        appended = self._workspaces.append_event(
            tenant_id=tenant_id,
            workspace_id=mapping["workspace_id"],
            principal_actor_id=principal_actor_id,
            event=observed,
        )
        return {
            **plan,
            "mode": "execute",
            "writes_performed": created_workspace or created_room or not appended["replayed"],
            "replayed": not created_workspace and not created_room and appended["replayed"],
            "observe_only": True,
            "legacy_authority_retained": True,
        }

    @staticmethod
    def rollback_projection(*, session_id: str) -> dict[str, Any]:
        return {
            "session_id": require_id(session_id, "share_session_id"),
            "new_projection_enabled": False,
            "legacy_session_deleted": False,
            "canonical_events_deleted": False,
            "reason_code": "legacy_projection_rollback",
        }

    @staticmethod
    def normalize_compatibility_request(value: Mapping[str, Any]) -> dict[str, Any]:
        aliases = {"sessionId": "session_id", "clientVersion": "client_version", "contractVersion": "contract_version"}
        result: dict[str, Any] = {}
        for key, item in value.items():
            canonical = aliases.get(key, key)
            if canonical in result and result[canonical] != item:
                raise ValueError("collaboration_legacy_alias_conflict")
            result[canonical] = item
        return result

    @staticmethod
    def deprecation_telemetry(*, client_version: str, contract_version: str, reason_code: str) -> dict[str, Any]:
        return {
            "client_version": require_id(client_version, "client_version"),
            "contract_version": require_id(contract_version, "contract_version"),
            "reason_code": require_id(reason_code, "deprecation_reason_code"),
            "contains_content": False,
        }

    def _session(self, tenant_id: str, principal_id: str, session_id: str) -> dict[str, Any]:
        session = self._legacy.get_session(require_id(session_id, "share_session_id"))
        if session is None:
            raise KeyError("collaboration_legacy_session_not_found")
        metadata = session.get("session_metadata") or {}
        if not isinstance(metadata, Mapping) or str(metadata.get("tenant_id") or "default") != tenant_id:
            raise PermissionError("collaboration_legacy_tenant_mismatch")
        if str(session.get("owner_user_id") or "") != principal_id:
            raise PermissionError("collaboration_legacy_owner_required")
        return dict(session)


__all__ = ["CollaborationLegacyMigrationService", "LegacyShareSessionReadPort"]
