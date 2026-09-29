from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlmodel import create_engine


def test_capability_grant_migration_is_restart_safe_and_reversible(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.eb1c2d3e4f5a_add_semantic_media_capability_grants"
    )
    database = create_engine("sqlite://")
    with database.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_semantic_state (id VARCHAR PRIMARY KEY)"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))

        migration.upgrade()
        migration.upgrade()
        inspector = inspect(connection)
        assert {"legacy_semantic_state", "semantic_media_capability_grants"} <= set(
            inspector.get_table_names()
        )
        assert {
            "id",
            "version",
            "owner_id",
            "tenant_id",
            "subject_id",
            "subject_role",
            "capability",
            "scope_kind",
            "scope_id",
            "direction",
            "data_type",
            "purpose",
            "epoch",
            "issued_at",
            "expires_at",
            "issuer",
            "signature",
            "revoked_at",
            "revoked_by",
            "revocation_version",
            "created_at",
            "updated_at",
        } == {
            column["name"]
            for column in inspector.get_columns("semantic_media_capability_grants")
        }

        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {"legacy_semantic_state"}
