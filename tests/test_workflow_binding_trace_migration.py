"""The binding migration that makes terminal trace reconciliation durable."""

from __future__ import annotations

from importlib import import_module

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

_MIGRATION = "migrations.versions.a1c3e5f7b9d2_add_workflow_binding_trace_reconciliation"
_TABLE = "workflow_control_bindings"
_INDEX = "ix_workflow_control_bindings_trace_pending"
_COLUMNS = (
    "trace_pending",
    "trace_pending_revision",
    "trace_projected_revision",
    "trace_cursor",
)


def _bindings_table(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            f"CREATE TABLE {_TABLE} ("
            "id VARCHAR(256) PRIMARY KEY, "
            "tenant_id VARCHAR(256) NOT NULL, "
            "workflow_id VARCHAR(256) NOT NULL, "
            "revision INTEGER NOT NULL DEFAULT 1"
            ")"
        )
    )


def _apply(monkeypatch: pytest.MonkeyPatch, connection: sa.Connection, name: str) -> None:
    migration = import_module(_MIGRATION)
    monkeypatch.setattr(
        migration,
        "op",
        Operations(MigrationContext.configure(connection)),
    )
    getattr(migration, name)()


def test_upgrade_adds_the_durable_trace_columns_and_pending_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _bindings_table(connection)

        _apply(monkeypatch, connection, "upgrade")

        columns = {column["name"] for column in inspect(connection).get_columns(_TABLE)}
        assert set(_COLUMNS) <= columns
        assert _INDEX in {index["name"] for index in inspect(connection).get_indexes(_TABLE)}


def test_upgrade_is_idempotent_against_a_partially_applied_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _bindings_table(connection)
        _apply(monkeypatch, connection, "upgrade")

        _apply(monkeypatch, connection, "upgrade")

        columns = {column["name"] for column in inspect(connection).get_columns(_TABLE)}
        assert set(_COLUMNS) <= columns


def test_existing_rows_default_to_no_pending_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _bindings_table(connection)
        connection.execute(
            sa.text(f"INSERT INTO {_TABLE} (id, tenant_id, workflow_id, revision) VALUES ('b-1', 't-1', 'w-1', 1)")
        )

        _apply(monkeypatch, connection, "upgrade")

        row = connection.execute(
            sa.text(f"SELECT trace_pending, trace_projected_revision, trace_cursor FROM {_TABLE} WHERE id = 'b-1'")
        ).one()
        assert not bool(row[0])
        assert int(row[1]) == 0
        assert str(row[2]) == ""


def test_downgrade_removes_every_column_it_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _bindings_table(connection)
        _apply(monkeypatch, connection, "upgrade")

        _apply(monkeypatch, connection, "downgrade")

        columns = {column["name"] for column in inspect(connection).get_columns(_TABLE)}
        assert not (set(_COLUMNS) & columns)


def test_upgrade_fails_closed_without_the_binding_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="prerequisite_missing"):
            _apply(monkeypatch, connection, "upgrade")
        assert inspect(connection).get_table_names() == []
