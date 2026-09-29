from __future__ import annotations

import importlib
import os
import uuid

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlmodel import create_engine


MIGRATION_MODULE = "migrations.versions.f7b8c9d0e1f2_add_sfu_broadcast_feature_flags"


def _install_operations(migration, connection, monkeypatch) -> None:
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))


def _assert_schema(connection) -> None:
    inspector = inspect(connection)
    assert {
        "sfu_broadcast_feature_flags",
        "sfu_broadcast_feature_flag_mutations",
    } <= set(inspector.get_table_names())
    flag_columns = {column["name"] for column in inspector.get_columns("sfu_broadcast_feature_flags")}
    assert {
        "tenant_id",
        "region",
        "room_cohort",
        "flag",
        "enabled",
        "rollout_stage",
        "version",
        "actor",
        "reason",
        "idempotency_key_digest",
        "audited_at",
    } <= flag_columns
    mutation_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("sfu_broadcast_feature_flag_mutations")
    }
    assert ("tenant_id", "idempotency_key_digest") in mutation_uniques


def test_sfu_broadcast_feature_flag_migration_additive_upgrade_and_downgrade(monkeypatch) -> None:
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_sfu_control (id VARCHAR PRIMARY KEY)"))
        _install_operations(migration, connection, monkeypatch)
        migration.upgrade()
        migration.upgrade()
        _assert_schema(connection)
        migration.downgrade()
        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {"legacy_sfu_control"}


def test_sfu_broadcast_feature_flag_migration_on_postgresql(monkeypatch) -> None:
    database_url = os.getenv("ANANTA_TEST_POSTGRESQL_URL")
    if not database_url:
        pytest.skip("ANANTA_TEST_POSTGRESQL_URL is required for the production-like migration gate")
    if not database_url.startswith(("postgresql://", "postgresql+")):
        pytest.fail("ANANTA_TEST_POSTGRESQL_URL must select PostgreSQL")

    migration = importlib.import_module(MIGRATION_MODULE)
    engine = sa.create_engine(database_url)
    schema = f"test_sfu_broadcast_{uuid.uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            _install_operations(migration, connection, monkeypatch)
            migration.upgrade()
            _assert_schema(connection)
            migration.downgrade()
            assert inspect(connection).get_table_names() == []
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
