from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError


def test_kanban_outbox_migration_is_restart_safe_and_reversible(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.b8d0f2a4c6e8_add_kanban_event_outbox"
    )
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "kanban_board_sequences",
            "kanban_event_outbox",
        }.issubset(inspector.get_table_names())
        primary_key = inspector.get_pk_constraint("kanban_event_outbox")
        assert primary_key["constrained_columns"] == ["board_id", "sequence"]

        connection.execute(
            sa.text(
                "INSERT INTO kanban_board_sequences "
                "(board_id, last_sequence, updated_at) "
                "VALUES ('hub', 1, CURRENT_TIMESTAMP)"
            )
        )
        insert = sa.text(
            "INSERT INTO kanban_event_outbox "
            "(board_id, sequence, event_id, task_id, revision, event_type, "
            "occurred_at, payload, dedupe_key) VALUES "
            "('hub', 1, '1', 'task-1', 1, 'kanban.card.created', "
            "CURRENT_TIMESTAMP, '{}', :dedupe)"
        )
        connection.execute(insert, {"dedupe": "a" * 64})
        with pytest.raises(IntegrityError):
            connection.execute(insert, {"dedupe": "b" * 64})

        migration.downgrade()
        migration.downgrade()
        assert "kanban_event_outbox" not in sa.inspect(
            connection
        ).get_table_names()

