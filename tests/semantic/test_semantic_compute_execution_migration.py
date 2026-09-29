from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine


def test_execution_control_migration_is_additive_and_reversible(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    migration = importlib.import_module("migrations.versions.f8a9b0c1d2e3_add_semantic_compute_execution_control")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_semantic_state (id VARCHAR PRIMARY KEY)"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert {
            "legacy_semantic_state",
            "semantic_compute_candidate_keys",
            "semantic_capability_advertisements",
            "semantic_compute_schedule_receipts",
            "semantic_compute_lease_mutations",
        } <= set(inspect(connection).get_table_names())
        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {"legacy_semantic_state"}
