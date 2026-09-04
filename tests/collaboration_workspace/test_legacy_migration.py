from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.collaboration_legacy_migration_service import CollaborationLegacyMigrationService
from agent.services.collaboration_workspace_store import CollaborationStoreConflict
from tests.collaboration_workspace.helpers import actor, service


class LegacySessions:
    def __init__(self) -> None:
        self.session = {
            "id": "session-a",
            "owner_user_id": "user-a",
            "title": "Legacy pair",
            "mode": "relay",
            "permissions": {"chat": True},
            "session_metadata": {"tenant_id": "tenant-a"},
        }

    def get_session(self, session_id: str):
        return dict(self.session) if session_id == "session-a" else None


def test_legacy_migration_is_dry_run_first_revision_bound_and_idempotent(tmp_path: Path) -> None:
    legacy = LegacySessions()
    workspaces = service(tmp_path / "state.sqlite3")
    migration = CollaborationLegacyMigrationService(legacy, workspaces)
    plan = migration.plan(
        tenant_id="tenant-a",
        principal_id="user-a",
        principal_actor_id="human-user-a",
        session_id="session-a",
    )
    assert plan["writes_performed"] is False
    assert workspaces.list_workspaces(tenant_id="tenant-a", principal_actor_id="human-user-a")["items"] == []
    first = migration.execute(
        tenant_id="tenant-a",
        principal_id="user-a",
        principal_actor_id="human-user-a",
        session_id="session-a",
        expected_source_revision=plan["mapping"]["source_revision"],
        owner=actor(),
    )
    replay = migration.execute(
        tenant_id="tenant-a",
        principal_id="user-a",
        principal_actor_id="human-user-a",
        session_id="session-a",
        expected_source_revision=plan["mapping"]["source_revision"],
        owner=actor(),
    )
    assert (first["observe_only"], first["legacy_authority_retained"]) == (True, True)
    assert replay["replayed"] is True
    legacy.session["title"] = "Changed after planning"
    changed = migration.plan(
        tenant_id="tenant-a",
        principal_id="user-a",
        principal_actor_id="human-user-a",
        session_id="session-a",
    )
    assert changed["conflicts"] == ["legacy_source_revision_changed"]
    with pytest.raises(CollaborationStoreConflict, match="revision_conflict"):
        migration.execute(
            tenant_id="tenant-a",
            principal_id="user-a",
            principal_actor_id="human-user-a",
            session_id="session-a",
            expected_source_revision=plan["mapping"]["source_revision"],
            owner=actor(),
        )


def test_legacy_compatibility_aliases_are_boundary_only_and_rollback_is_nondestructive() -> None:
    normalized = CollaborationLegacyMigrationService.normalize_compatibility_request(
        {"sessionId": "session-a", "clientVersion": "v1", "contract_version": "v2"}
    )
    assert normalized == {"session_id": "session-a", "client_version": "v1", "contract_version": "v2"}
    with pytest.raises(ValueError, match="alias_conflict"):
        CollaborationLegacyMigrationService.normalize_compatibility_request(
            {"sessionId": "session-a", "session_id": "session-b"}
        )
    rollback = CollaborationLegacyMigrationService.rollback_projection(session_id="session-a")
    assert rollback["legacy_session_deleted"] is False
    assert rollback["canonical_events_deleted"] is False
    telemetry = CollaborationLegacyMigrationService.deprecation_telemetry(
        client_version="v1", contract_version="v2", reason_code="legacy_alias_used"
    )
    assert telemetry["contains_content"] is False
