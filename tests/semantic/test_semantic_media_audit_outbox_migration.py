from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlmodel import create_engine


def test_semantic_media_audit_outbox_migration_is_restart_safe_and_reversible(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.d9e0f1a2b3c4_add_semantic_media_audit_outbox"
    )
    database = create_engine("sqlite://")
    with database.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_semantic_media (id VARCHAR PRIMARY KEY)"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))

        migration.upgrade()
        migration.upgrade()

        inspector = inspect(connection)
        assert {"legacy_semantic_media", "semantic_media_audit_outbox"} <= set(
            inspector.get_table_names()
        )
        assert {column["name"] for column in inspector.get_columns("semantic_media_audit_outbox")} == {
            "id",
            "event_id",
            "idempotency_digest",
            "tenant_digest",
            "scope_digest",
            "event_type",
            "transition",
            "reason_code",
            "epoch",
            "contract_ref",
            "lease_ref",
            "job_ref",
            "created_at_ms",
            "expires_at_ms",
            "available_at_ms",
        }

        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {"legacy_semantic_media"}
