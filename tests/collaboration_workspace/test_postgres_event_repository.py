from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from agent.services.collaboration_postgres_event_repository import PostgresCollaborationEventRepository
from agent.services.collaboration_workspace_store import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import canonical_digest
from tests.collaboration_workspace.helpers import build_event

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
            "collaboration_shared_projection_checkpoints",
            "collaboration_shared_outbox",
            "collaboration_durable_events",
            "collaboration_event_streams",
        ):
            connection.execute(sa.text(f"DELETE FROM {table}"))


def _event(index: int, *, workspace: str = "workspace-a", room_id: str = "room-main"):
    event = build_event(
        workspace_id=workspace,
        room_id=room_id,
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"text": f"message-{index}"},
        idempotency_key=f"idempotency-{index}",
    )
    return {**event, "event_id": f"event-{index}"}


def test_migration_supports_downgrade_and_upgrade(postgres_engine) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = _DATABASE_URL
    assert "collaboration_durable_events" in sa.inspect(postgres_engine).get_table_names()

    command.downgrade(config, "b7d9f1a3c5e8")
    assert "collaboration_durable_events" not in sa.inspect(postgres_engine).get_table_names()

    command.upgrade(config, "head")
    assert {
        "collaboration_event_streams",
        "collaboration_durable_events",
        "collaboration_shared_outbox",
        "collaboration_shared_projection_checkpoints",
        "collaboration_shared_cursors",
        "collaboration_shared_control_grants",
        "collaboration_shared_presence",
        "collaboration_shared_cache",
    }.issubset(sa.inspect(postgres_engine).get_table_names())


def test_two_repository_instances_append_monotonic_gap_free_events(postgres_engine) -> None:
    first = PostgresCollaborationEventRepository(postgres_engine, clock=lambda: 100.0)
    second = PostgresCollaborationEventRepository(postgres_engine, clock=lambda: 100.0)
    first.admit_workspace(tenant_id="tenant-a", workspace_id="workspace-a")

    def append(index: int):
        repository = first if index % 2 else second
        return repository.append(tenant_id="tenant-a", event=_event(index))[0]

    with ThreadPoolExecutor(max_workers=12) as pool:
        admitted = list(pool.map(append, range(1, 49)))

    assert sorted(event["sequence"] for event in admitted) == list(range(1, 49))
    stored = second.events(tenant_id="tenant-a", workspace_id="workspace-a")
    assert [event["sequence"] for event in stored] == list(range(1, 49))
    assert len(first.pending_outbox(tenant_id="tenant-a")) == 48


def test_idempotency_conflict_checkpoint_cas_and_scope_isolation(postgres_engine) -> None:
    first = PostgresCollaborationEventRepository(postgres_engine, clock=lambda: 200.0)
    second = PostgresCollaborationEventRepository(postgres_engine, clock=lambda: 200.0)
    for tenant in ("tenant-b", "tenant-c"):
        first.admit_workspace(tenant_id=tenant, workspace_id="workspace-shared-name")
    event = _event(100, workspace="workspace-shared-name")
    admitted, replayed = first.append(tenant_id="tenant-b", event=event)
    replay, was_replayed = second.append(tenant_id="tenant-b", event=event)
    assert replayed is False and was_replayed is True
    assert replay == admitted
    changed_payload = {"text": "mutated"}
    with pytest.raises(CollaborationStoreConflict, match="idempotency_conflict"):
        second.append(
            tenant_id="tenant-b",
            event={
                **event,
                "payload": changed_payload,
                "payload_digest": canonical_digest(changed_payload),
            },
        )
    assert second.events(tenant_id="tenant-c", workspace_id="workspace-shared-name") == []
    other_room = _event(101, workspace="workspace-shared-name", room_id="room-secondary")
    first.append(tenant_id="tenant-b", event=other_room)
    assert [
        value["event_id"]
        for value in second.events(
            tenant_id="tenant-b",
            workspace_id="workspace-shared-name",
            room_id="room-secondary",
        )
    ] == ["event-101"]
    checkpoint = first.advance_checkpoint(
        tenant_id="tenant-b",
        workspace_id="workspace-shared-name",
        projection_name="timeline",
        checkpoint=1,
        state_digest="a" * 64,
        expected_revision=0,
    )
    assert checkpoint["revision"] == 1
    with pytest.raises(CollaborationStoreConflict, match="revision_conflict"):
        second.advance_checkpoint(
            tenant_id="tenant-b",
            workspace_id="workspace-shared-name",
            projection_name="timeline",
            checkpoint=1,
            state_digest="b" * 64,
            expected_revision=0,
        )


def test_unadmitted_workspace_and_cross_tenant_reads_fail_closed(postgres_engine) -> None:
    repository = PostgresCollaborationEventRepository(postgres_engine)
    with pytest.raises(KeyError, match="workspace_stream_not_admitted"):
        repository.append(tenant_id="tenant-missing", event=_event(200, workspace="workspace-missing"))
    assert repository.events(tenant_id="tenant-missing", workspace_id="workspace-a") == []
