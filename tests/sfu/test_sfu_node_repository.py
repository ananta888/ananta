from __future__ import annotations

import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models import SfuNodeDB, SfuNodeMutationDB
from agent.repositories.sfu_node_repository import (
    SfuNodeRepositoryError,
    SqlSfuNodeRepository,
)


NOW = 1_800_000_000.0
CURSOR_KEY = b"test-sfu-node-cursor-signing-key"


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[SfuNodeDB.__table__, SfuNodeMutationDB.__table__],
    )
    return engine


def _repository(*, engine=None, clock=lambda: NOW):
    return SqlSfuNodeRepository(
        db_engine=engine or _engine(),
        clock=clock,
        cursor_signing_key=CURSOR_KEY,
    )


def _enroll(
    repository,
    node_id="node-a",
    *,
    tenant_id="tenant-a",
    cluster_id="cluster-a",
    fencing_token=1,
):
    return repository.enroll_node(
        tenant_id=tenant_id,
        cluster_id=cluster_id,
        node_id=node_id,
        runtime_identity_id=f"identity-{node_id}",
        region="eu-central",
        adapter_name="livekit",
        adapter_version="1.9.0",
        protocol_version="1",
        capability_digest="sha256:" + node_id.encode().hex().ljust(64, "0")[:64],
        expected_version=0,
        fencing_token=fencing_token,
    )


def _observe(repository, record, *, fencing_token=None, ttl=10.0):
    return repository.record_observation(
        tenant_id=record.tenant_id,
        cluster_id=record.cluster_id,
        node_id=record.node_id,
        observation_id=f"observation-v{record.version}",
        region="eu-central",
        adapter_name="livekit",
        adapter_version="1.9.1",
        protocol_version="1",
        capability_digest="sha256:" + "a" * 64,
        health_status="healthy",
        observation_ttl_seconds=ttl,
        expected_version=record.version,
        fencing_token=record.fencing_token if fencing_token is None else fencing_token,
    )


def test_migration_is_based_on_runtime_identity_revision_and_round_trips():
    migration = importlib.import_module(
        "migrations.versions.19d0e1f2a3b4_add_sfu_nodes"
    )
    assert migration.down_revision == "08c9d0e1f2a3"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert {"sfu_nodes", "sfu_node_mutations"}.issubset(
            inspect(connection).get_table_names()
        )
        migration.downgrade()
        assert "sfu_nodes" not in inspect(connection).get_table_names()


def test_restart_preserves_enrollment_capability_health_drain_and_version():
    engine = _engine()
    first_hub = _repository(engine=engine)
    enrolled = _enroll(first_hub)
    observed = _observe(first_hub, enrolled, fencing_token=4)
    drained = first_hub.set_drain(
        tenant_id="tenant-a",
        cluster_id="cluster-a",
        node_id="node-a",
        drain_state="draining",
        reason="planned maintenance",
        expected_version=observed.version,
        fencing_token=5,
    )

    restarted_hub = _repository(engine=engine)
    restored = restarted_hub.get_node(
        tenant_id="tenant-a",
        cluster_id="cluster-a",
        node_id="node-a",
    )
    assert restored is not None
    assert restored.runtime_identity_id == "identity-node-a"
    assert restored.adapter_version == "1.9.1"
    assert restored.capability_digest == "sha256:" + "a" * 64
    assert restored.health_status == "healthy"
    assert restored.drain_state == "draining"
    assert restored.fencing_token == 5
    assert restored.version == drained.version == 3


def test_observation_expiry_is_explicit_and_never_removes_the_node():
    now = [NOW]
    repository = _repository(clock=lambda: now[0])
    enrolled = _enroll(repository)
    assert enrolled.observation_status == "unknown"
    assert enrolled.effective_health == "unknown"
    observed = _observe(repository, enrolled, ttl=5)
    assert observed.observation_status == "current"
    assert observed.effective_health == "healthy"

    now[0] += 5
    stale = repository.get_node(
        tenant_id="tenant-a",
        cluster_id="cluster-a",
        node_id="node-a",
    )
    assert stale is not None
    assert stale.observation_status == "stale"
    assert stale.health_status == "healthy"
    assert stale.effective_health == "unknown"
    assert repository.list_nodes(tenant_id="tenant-a", cluster_id="cluster-a").items == (
        stale,
    )


def test_two_hubs_cas_fencing_and_revocation_are_monotonic_and_sticky():
    engine = _engine()
    observation_hub = _repository(engine=engine)
    drain_hub = _repository(engine=engine)
    enrolled = _enroll(observation_hub, fencing_token=2)

    observed = _observe(observation_hub, enrolled, fencing_token=7)
    with pytest.raises(SfuNodeRepositoryError, match="sfu_node_version_conflict"):
        drain_hub.set_drain(
            tenant_id="tenant-a",
            cluster_id="cluster-a",
            node_id="node-a",
            drain_state="draining",
            reason="stale hub command",
            expected_version=enrolled.version,
            fencing_token=9,
        )
    drained = drain_hub.set_drain(
        tenant_id="tenant-a",
        cluster_id="cluster-a",
        node_id="node-a",
        drain_state="draining",
        reason="fresh hub command",
        expected_version=observed.version,
        fencing_token=9,
    )
    with pytest.raises(SfuNodeRepositoryError, match="sfu_node_fencing_conflict"):
        _observe(observation_hub, drained, fencing_token=8)

    revoked = observation_hub.revoke_node(
        tenant_id="tenant-a",
        cluster_id="cluster-a",
        node_id="node-a",
        reason="runtime identity revoked",
        expected_version=drained.version,
        fencing_token=10,
    )
    assert revoked.revoked is True
    assert revoked.version == 4
    assert revoked.fencing_token == 10
    with pytest.raises(SfuNodeRepositoryError, match="sfu_node_revoked"):
        _observe(drain_hub, revoked, fencing_token=11)
    persisted = observation_hub.get_node(
        tenant_id="tenant-a", cluster_id="cluster-a", node_id="node-a"
    )
    assert persisted is not None
    assert persisted.revoked is True
    assert persisted.version == 4


def test_sorted_paginated_list_and_watch_are_scope_bound_and_tamper_evident():
    repository = _repository()
    _enroll(repository, "node-c")
    _enroll(repository, "node-a")
    _enroll(repository, "node-b")
    _enroll(repository, "node-other-cluster", cluster_id="cluster-b")
    _enroll(repository, "node-other-tenant", tenant_id="tenant-b")

    first = repository.list_nodes(
        tenant_id="tenant-a", cluster_id="cluster-a", limit=2
    )
    assert [node.node_id for node in first.items] == ["node-a", "node-b"]
    assert first.next_cursor is not None
    second = repository.list_nodes(
        tenant_id="tenant-a",
        cluster_id="cluster-a",
        limit=2,
        cursor=first.next_cursor,
    )
    assert [node.node_id for node in second.items] == ["node-c"]
    assert second.next_cursor is None

    with pytest.raises(SfuNodeRepositoryError, match="sfu_node_cursor_scope_mismatch"):
        repository.list_nodes(
            tenant_id="tenant-a",
            cluster_id="cluster-b",
            limit=2,
            cursor=first.next_cursor,
        )
    raw, signature = first.next_cursor.split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(SfuNodeRepositoryError, match="sfu_node_cursor_invalid"):
        repository.list_nodes(
            tenant_id="tenant-a",
            cluster_id="cluster-a",
            limit=2,
            cursor=f"{raw}.{tampered_signature}",
        )

    watch_first = repository.watch_nodes(
        tenant_id="tenant-a", cluster_id="cluster-a", limit=2
    )
    assert [change.node.node_id for change in watch_first.changes] == ["node-c", "node-a"]
    assert watch_first.has_more is True
    watch_second = repository.watch_nodes(
        tenant_id="tenant-a",
        cluster_id="cluster-a",
        limit=2,
        cursor=watch_first.cursor,
    )
    assert [change.node.node_id for change in watch_second.changes] == ["node-b"]
    assert watch_second.has_more is False
    assert [change.sequence for change in watch_first.changes + watch_second.changes] == sorted(
        change.sequence for change in watch_first.changes + watch_second.changes
    )


def test_cross_cluster_reads_return_no_foreign_node_or_watch_event():
    repository = _repository()
    _enroll(repository, "node-a", cluster_id="cluster-a")
    _enroll(repository, "node-b", cluster_id="cluster-b")
    assert repository.get_node(
        tenant_id="tenant-a", cluster_id="cluster-b", node_id="node-a"
    ) is None
    cluster_b = repository.watch_nodes(tenant_id="tenant-a", cluster_id="cluster-b")
    assert [change.node.node_id for change in cluster_b.changes] == ["node-b"]


def test_database_failover_failure_is_explicit_without_memory_fallback():
    unavailable_engine = create_engine(
        "sqlite:////proc/ananta-sfu-directory-unavailable/directory.sqlite"
    )
    repository = _repository(engine=unavailable_engine)
    with pytest.raises(SfuNodeRepositoryError, match="sfu_node_store_unavailable"):
        repository.list_nodes(tenant_id="tenant-a", cluster_id="cluster-a")
