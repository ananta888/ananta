from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from agent.services.collaboration_live_control_service import CollaborationLiveControlService
from agent.services.collaboration_postgres_coordination_repository import (
    PostgresCollaborationCoordinationRepository,
)
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationStoreConflict, CollaborationWorkspaceStore
from tests.collaboration_workspace.helpers import actor, room, service

_DATABASE_URL = os.environ.get("COLLABORATION_POSTGRES_TEST_URL", "").strip()
pytestmark = pytest.mark.skipif(not _DATABASE_URL, reason="collaboration_postgres_test_url_not_configured")


@pytest.fixture(scope="module")
def postgres_engine():
    guard_engine = sa.create_engine(_DATABASE_URL, pool_pre_ping=True)
    guard = guard_engine.connect()
    guard.exec_driver_sql("SELECT pg_advisory_lock(2044597617)")
    config = Config("alembic.ini")
    config.attributes["database_url"] = _DATABASE_URL
    command.upgrade(config, "head")
    engine = sa.create_engine(_DATABASE_URL, pool_pre_ping=True)
    yield engine
    engine.dispose()
    guard.exec_driver_sql("SELECT pg_advisory_unlock(2044597617)")
    guard.close()
    guard_engine.dispose()


@pytest.fixture(autouse=True)
def clean_repository_rows(postgres_engine):
    with postgres_engine.begin() as connection:
        for table in (
            "collaboration_shared_cursors",
            "collaboration_shared_control_grants",
            "collaboration_shared_presence",
            "collaboration_shared_cache",
        ):
            connection.execute(sa.text(f"DELETE FROM {table}"))


def _live_services(database: Path, engine, now: list[float]):
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-live",
        principal_id="user-a",
        title="Shared live state",
        owner=actor(),
        workspace_id="workspace-live",
    )
    workspaces.create_room(
        tenant_id="tenant-live",
        workspace_id="workspace-live",
        principal_actor_id="human-user-a",
        room=room("room-live"),
    )
    for suffix in ("b", "c"):
        workspaces.put_membership(
            tenant_id="tenant-live",
            workspace_id="workspace-live",
            principal_actor_id="human-user-a",
            actor=actor(f"human-user-{suffix}", subject=f"user-{suffix}"),
            role="member",
            status="active",
        )
    state_a = PostgresCollaborationCoordinationRepository(engine)
    state_b = PostgresCollaborationCoordinationRepository(engine)
    local_store = CollaborationWorkspaceStore(database)
    first = CollaborationLiveControlService(
        local_store,
        policy=CollaborationWorkspacePolicy(),
        state=state_a,
        clock=lambda: now[0],
    )
    second = CollaborationLiveControlService(
        local_store,
        policy=CollaborationWorkspacePolicy(),
        state=state_b,
        clock=lambda: now[0],
    )
    return first, second


def test_two_hub_services_share_cursor_and_fence_control_grant(tmp_path: Path, postgres_engine) -> None:
    now = [100.0]
    first, second = _live_services(tmp_path / "state.sqlite3", postgres_engine, now)
    first.publish_cursor(
        tenant_id="tenant-live",
        workspace_id="workspace-live",
        room_id="room-live",
        principal_actor_id="human-user-a",
        view_id="editor-main",
        x=0.25,
        y=0.75,
        epoch=1,
        ttl_seconds=10,
    )
    visible = second.cursors(
        tenant_id="tenant-live",
        workspace_id="workspace-live",
        room_id="room-live",
        principal_actor_id="human-user-b",
        view_id="editor-main",
    )
    assert [item["actor_binding_id"] for item in visible["items"]] == ["human-user-a"]

    def grant(controller: str):
        live = first if controller == "human-user-a" else second
        return live.grant_control(
            tenant_id="tenant-live",
            workspace_id="workspace-live",
            room_id="room-live",
            principal_actor_id="human-user-b",
            controller_actor_binding_id=controller,
            session_id=f"session-{controller}",
            view_id="editor-main",
            epoch=1,
            expected_revision=0,
            ttl_seconds=30,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(grant, controller) for controller in ("human-user-a", "human-user-c")]
    results = []
    conflicts = []
    for future in futures:
        try:
            results.append(future.result())
        except CollaborationStoreConflict as exc:
            conflicts.append(str(exc))
    assert len(results) == 1
    assert conflicts == ["collaboration_control_revision_conflict"]
    assert second.current_grant(
        tenant_id="tenant-live",
        workspace_id="workspace-live",
        principal_actor_id="human-user-b",
    ) == results[0]
    now[0] = 131.0
    renewed = second.grant_control(
        tenant_id="tenant-live",
        workspace_id="workspace-live",
        room_id="room-live",
        principal_actor_id="human-user-b",
        controller_actor_binding_id="human-user-a",
        session_id="session-renewed",
        view_id="editor-main",
        epoch=2,
        expected_revision=0,
        ttl_seconds=30,
    )
    assert renewed["revision"] == 1


def test_presence_and_cache_keys_are_tenant_workspace_qualified(postgres_engine) -> None:
    repository = PostgresCollaborationCoordinationRepository(postgres_engine)
    for tenant, marker in (("tenant-scope-a", "a"), ("tenant-scope-b", "b")):
        repository.renew_presence(
            tenant_id=tenant,
            workspace_id="workspace-shared-name",
            actor_binding_id="actor-shared-name",
            lease_id=f"lease-{marker}",
            epoch=1,
            expires_at=200.0,
        )
        repository.compare_and_set_cache(
            tenant_id=tenant,
            workspace_id="workspace-shared-name",
            namespace="policy",
            cache_key="same-key",
            expected_revision=0,
            expires_at=200.0,
            payload={"marker": marker},
        )
    assert repository.presence_values(
        tenant_id="tenant-scope-a", workspace_id="workspace-shared-name", now=100.0
    )[0]["lease_id"] == "lease-a"
    assert repository.cache_value(
        tenant_id="tenant-scope-b",
        workspace_id="workspace-shared-name",
        namespace="policy",
        cache_key="same-key",
        now=100.0,
    )["payload"] == {"marker": "b"}
    with pytest.raises(CollaborationStoreConflict, match="cache_revision_conflict"):
        repository.compare_and_set_cache(
            tenant_id="tenant-scope-a",
            workspace_id="workspace-shared-name",
            namespace="policy",
            cache_key="same-key",
            expected_revision=0,
            expires_at=300.0,
            payload={"marker": "stale"},
        )


def test_shared_state_database_failure_has_no_local_fallback(tmp_path: Path, postgres_engine) -> None:
    now = [100.0]
    database = tmp_path / "fail-closed.sqlite3"
    _first, _second = _live_services(database, postgres_engine, now)
    # The live service owns no hidden secondary state; a repository exception is propagated.
    class UnavailableState:
        def put_cursor(self, **_kwargs):
            raise RuntimeError("shared_coordination_unavailable")

    first = CollaborationLiveControlService(
        CollaborationWorkspaceStore(database),
        policy=CollaborationWorkspacePolicy(),
        state=UnavailableState(),
        clock=lambda: now[0],
    )
    with pytest.raises(RuntimeError, match="shared_coordination_unavailable"):
        first.publish_cursor(
            tenant_id="tenant-live",
            workspace_id="workspace-live",
            room_id="room-live",
            principal_actor_id="human-user-a",
            view_id="editor-main",
            x=0.5,
            y=0.5,
            epoch=1,
            ttl_seconds=10,
        )
