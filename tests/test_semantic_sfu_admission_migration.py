from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlmodel import create_engine


def test_sfu_admission_migration_upgrade_and_downgrade(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.e6f7a8b9c0d1_add_semantic_sfu_admission_state")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_semantic (id VARCHAR PRIMARY KEY)"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        tables = set(inspect(connection).get_table_names())
        assert {
            "legacy_semantic",
            "semantic_sfu_room_states",
            "semantic_sfu_admission_receipts",
        } <= tables
        migration.upgrade()
        migration.downgrade()
        tables = set(inspect(connection).get_table_names())
        assert tables == {"legacy_semantic"}
