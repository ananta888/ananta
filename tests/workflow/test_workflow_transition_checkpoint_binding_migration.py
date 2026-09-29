"""The migration that gives checkpoint bindings their own fenced table."""

from __future__ import annotations

from importlib import import_module

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

_MIGRATION = "migrations.versions.c3e5a7b9d1f4_add_workflow_transition_checkpoint_bindings"
_TABLE = "workflow_transition_checkpoint_bindings"
_PREREQUISITE = "workflow_transition_effects"
_UNIQUE = (
    "uq_workflow_transition_checkpoint_bind_effect",
    "uq_workflow_transition_checkpoint_bind_fence",
    "uq_workflow_transition_checkpoint_bind_attempt",
    "uq_workflow_transition_checkpoint_bind_revision",
)


def _prerequisite(connection: sa.Connection) -> None:
    connection.execute(sa.text(f"CREATE TABLE {_PREREQUISITE} (id VARCHAR(256) PRIMARY KEY)"))


def _apply(monkeypatch: pytest.MonkeyPatch, connection: sa.Connection, name: str) -> None:
    migration = import_module(_MIGRATION)
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
    getattr(migration, name)()


def test_upgrade_creates_the_binding_table_with_every_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _prerequisite(connection)

        _apply(monkeypatch, connection, "upgrade")

        inspector = inspect(connection)
        assert _TABLE in set(inspector.get_table_names())
        constraints = {str(item["name"]) for item in inspector.get_unique_constraints(_TABLE)}
        assert set(_UNIQUE) <= constraints
        indexes = {str(item["name"]) for item in inspector.get_indexes(_TABLE)}
        assert "ix_workflow_transition_checkpoint_bind_checkpoint" in indexes


def test_upgrade_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _prerequisite(connection)
        _apply(monkeypatch, connection, "upgrade")

        _apply(monkeypatch, connection, "upgrade")

        assert _TABLE in set(inspect(connection).get_table_names())


def test_downgrade_removes_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _prerequisite(connection)
        _apply(monkeypatch, connection, "upgrade")

        _apply(monkeypatch, connection, "downgrade")

        assert _TABLE not in set(inspect(connection).get_table_names())


def test_upgrade_fails_closed_without_its_prerequisite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A binding without the effect table it belongs to is meaningless."""

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="prerequisite_missing"):
            _apply(monkeypatch, connection, "upgrade")
        assert inspect(connection).get_table_names() == []
