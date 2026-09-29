from __future__ import annotations

import importlib
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlmodel import create_engine


MIGRATION_MODULE = "migrations.versions.3bf2c3d4e5f6_add_sfu_broadcast_projections"
PROJECTION_TABLES = (
    "sfu_broadcast_audiences",
    "sfu_receiver_groups",
    "sfu_fanout_routes",
)
DIGEST = "a" * 64


def _install_operations(migration, connection, monkeypatch) -> None:
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))


def _create_room_state_table(connection) -> sa.Table:
    metadata = sa.MetaData()
    table = sa.Table(
        "semantic_sfu_room_states",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("participants", sa.JSON(), nullable=False),
        sa.Column("publications", sa.JSON(), nullable=False),
        sa.Column("subscriptions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("tenant_id", "session_id"),
    )
    metadata.create_all(connection)
    now = time.time()
    connection.execute(table.insert().values(
        id="room-state-a",
        tenant_id="tenant-a",
        session_id="session-a",
        revision=3,
        participants=[],
        publications=[{"publication_id": "publication-a"}],
        subscriptions=[{"subscription_id": "subscription-b"}],
        created_at=now,
        updated_at=now,
    ))
    return table


def _common(**overrides) -> dict:
    now = time.time()
    values = {
        "tenant_id": "tenant-a",
        "session_id": "session-a",
        "room_state_id": "room-state-a",
        "room_state_revision": 3,
        "status": "active",
        "ttl_seconds": 60,
        "retention_seconds": 120,
        "retention_status": "live",
        "expires_at": now + 60,
        "retain_until": now + 180,
        "tombstoned_at": None,
        "tombstone_reason": None,
        "fencing_token": 7,
        "version": 1,
        "audit_actor_ref": "hub:test",
        "audit_reason": "migration_acceptance",
        "request_digest": DIGEST,
        "idempotency_key_digest": "b" * 64,
        "created_at": now,
        "updated_at": now,
        "audited_at": now,
    }
    values.update(overrides)
    return values


def _audience(identifier: str = "audience-a", **overrides) -> dict:
    values = _common(
        id=identifier,
        audience_ref="audience-ref-a",
        publication_ref="publication-a",
        audience_digest="c" * 64,
        policy_digest="d" * 64,
        membership_digest="e" * 64,
        policy_epoch=2,
        membership_epoch=3,
        key_epoch=4,
    )
    values.update(overrides)
    return values


def _receiver_group(identifier: str = "receiver-group-b", **overrides) -> dict:
    values = _common(
        id=identifier,
        receiver_group_ref="receiver-group-ref-b",
        subscription_ref="subscription-b",
        group_digest="f" * 64,
        membership_digest="1" * 64,
        key_digest="2" * 64,
        membership_epoch=3,
        key_epoch=4,
        topology_epoch=5,
    )
    values.update(overrides)
    return values


def _route(identifier: str = "route-a-b", **overrides) -> dict:
    values = _common(
        id=identifier,
        route_ref="route-ref-a-b",
        audience_projection_id="audience-a",
        receiver_group_projection_id="receiver-group-b",
        publication_ref="publication-a",
        subscription_ref="subscription-b",
        route_digest="3" * 64,
        policy_digest="4" * 64,
        membership_digest="5" * 64,
        key_digest="6" * 64,
        policy_epoch=2,
        membership_epoch=3,
        key_epoch=4,
        route_epoch=5,
        topology_epoch=6,
    )
    values.update(overrides)
    return values


def _tables(connection) -> tuple[sa.Table, sa.Table, sa.Table]:
    metadata = sa.MetaData()
    return tuple(
        sa.Table(name, metadata, autoload_with=connection)
        for name in PROJECTION_TABLES
    )


def _assert_schema(connection) -> None:
    inspector = inspect(connection)
    assert set(PROJECTION_TABLES) <= set(inspector.get_table_names())
    for table in PROJECTION_TABLES:
        columns = {column["name"] for column in inspector.get_columns(table)}
        assert {
            "tenant_id",
            "session_id",
            "room_state_id",
            "room_state_revision",
            "status",
            "ttl_seconds",
            "retention_seconds",
            "retention_status",
            "expires_at",
            "retain_until",
            "tombstoned_at",
            "fencing_token",
            "version",
            "request_digest",
            "created_at",
            "updated_at",
            "audited_at",
        } <= columns
        assert not {"participants", "permissions", "authorized_subscriber_ids"} & columns


def _expect_integrity(connection, statement) -> None:
    with pytest.raises(sa.exc.IntegrityError):
        with connection.begin_nested():
            connection.execute(statement)


def _exercise_projection_guards(connection) -> None:
    audiences, groups, routes = _tables(connection)
    connection.execute(audiences.insert().values(**_audience()))
    connection.execute(groups.insert().values(**_receiver_group()))
    connection.execute(routes.insert().values(**_route()))

    _expect_integrity(
        connection,
        audiences.insert().values(**_audience(
            "stale-audience",
            room_state_revision=2,
            status="pending",
        )),
    )
    _expect_integrity(
        connection,
        audiences.insert().values(**_audience(
            "orphan-publication",
            publication_ref="invented-publication",
            status="pending",
        )),
    )
    _expect_integrity(
        connection,
        groups.insert().values(**_receiver_group(
            "orphan-subscription",
            subscription_ref="invented-subscription",
            status="pending",
        )),
    )
    _expect_integrity(
        connection,
        routes.insert().values(**_route(
            "expired-route",
            status="active",
            created_at=time.time() - 120,
            expires_at=time.time() - 60,
            retain_until=time.time() + 60,
        )),
    )
    _expect_integrity(
        connection,
        audiences.update().where(audiences.c.id == "audience-a").values(
            version=1,
            fencing_token=6,
            updated_at=time.time(),
        ),
    )


def test_sfu_broadcast_migration_upgrade_guards_and_safe_rollback(monkeypatch) -> None:
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_sfu_control (id VARCHAR PRIMARY KEY)"))
        _create_room_state_table(connection)
        base_columns = {
            column["name"]
            for column in inspect(connection).get_columns("semantic_sfu_room_states")
        }
        _install_operations(migration, connection, monkeypatch)
        migration.upgrade()
        migration.upgrade()
        _assert_schema(connection)
        _exercise_projection_guards(connection)

        with pytest.raises(RuntimeError, match="refusing to drop live"):
            migration.downgrade()

        audiences, groups, routes = _tables(connection)
        connection.execute(routes.delete())
        connection.execute(groups.delete())
        connection.execute(audiences.delete())
        migration.downgrade()
        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {
            "legacy_sfu_control",
            "semantic_sfu_room_states",
        }
        assert {
            column["name"]
            for column in inspect(connection).get_columns("semantic_sfu_room_states")
        } == base_columns


def test_sfu_broadcast_concurrent_active_insert_is_serialized(tmp_path, monkeypatch) -> None:
    migration = importlib.import_module(MIGRATION_MODULE)
    database = tmp_path / "sfu-broadcast.sqlite"
    engine = create_engine(
        f"sqlite:///{database}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with engine.begin() as connection:
        _create_room_state_table(connection)
        _install_operations(migration, connection, monkeypatch)
        migration.upgrade()

    barrier = Barrier(2)

    def insert_projection(identifier: str) -> str:
        barrier.wait()
        try:
            with engine.begin() as connection:
                audience = sa.Table(
                    "sfu_broadcast_audiences",
                    sa.MetaData(),
                    autoload_with=connection,
                )
                connection.execute(audience.insert().values(**_audience(identifier)))
            return "inserted"
        except sa.exc.IntegrityError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(insert_projection, ("audience-race-a", "audience-race-b")))
    assert sorted(results) == ["conflict", "inserted"]
    engine.dispose()


def test_sfu_broadcast_migration_on_postgresql(monkeypatch) -> None:
    database_url = os.getenv("ANANTA_TEST_POSTGRESQL_URL")
    if not database_url:
        pytest.skip("ANANTA_TEST_POSTGRESQL_URL is required for the production-like migration gate")
    if not database_url.startswith(("postgresql://", "postgresql+")):
        pytest.fail("ANANTA_TEST_POSTGRESQL_URL must select PostgreSQL")

    migration = importlib.import_module(MIGRATION_MODULE)
    engine = sa.create_engine(database_url)
    schema = f"test_sfu_broadcast_projection_{uuid.uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            _create_room_state_table(connection)
            _install_operations(migration, connection, monkeypatch)
            migration.upgrade()
            _assert_schema(connection)
            _exercise_projection_guards(connection)

            with pytest.raises(RuntimeError, match="refusing to drop live"):
                migration.downgrade()

            audiences, groups, routes = _tables(connection)
            connection.execute(routes.delete())
            connection.execute(groups.delete())
            connection.execute(audiences.delete())
            migration.downgrade()
            assert set(inspect(connection).get_table_names()) == {
                "semantic_sfu_room_states"
            }
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
