from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


def test_semantic_relay_migrations_upgrade_and_downgrade_without_touching_legacy(monkeypatch) -> None:
    database = create_engine("sqlite://")
    with database.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_sessions (id VARCHAR PRIMARY KEY)"))
        relay = importlib.import_module("migrations.versions.d8e9f0a1b2c3_add_semantic_relay_store")
        metadata = importlib.import_module("migrations.versions.e9f0a1b2c3d4_add_semantic_relay_wire_metadata")
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(relay, "op", operations)
        monkeypatch.setattr(metadata, "op", operations)

        relay.upgrade()
        metadata.upgrade()
        inspector = inspect(connection)
        assert {"legacy_sessions", "semantic_relay_cursors", "semantic_relay_envelopes"} <= set(
            inspector.get_table_names()
        )
        assert {"sequence", "compression", "security_algorithm", "key_id"} <= {
            str(column["name"]) for column in inspector.get_columns("semantic_relay_envelopes")
        }

        metadata.downgrade()
        assert not {"sequence", "compression", "security_algorithm", "key_id"} & {
            str(column["name"]) for column in inspect(connection).get_columns("semantic_relay_envelopes")
        }
        relay.downgrade()
        assert set(inspect(connection).get_table_names()) == {"legacy_sessions"}
