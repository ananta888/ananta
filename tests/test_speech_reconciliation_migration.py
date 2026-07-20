from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlmodel import create_engine


def test_speech_reconciliation_migration_upgrade_and_downgrade_preserve_legacy(monkeypatch) -> None:
    db = create_engine("sqlite://")
    with db.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_voice (id VARCHAR PRIMARY KEY)"))
        migration = importlib.import_module("migrations.versions.a2b3c4d5e6f7_add_speech_reconciliation_jobs")
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert {
            "legacy_voice",
            "speech_reconciliation_jobs",
            "speech_reconciliation_attempts",
            "speech_reconciliation_budget_ledgers",
            "speech_reconciliation_checkpoints",
            "speech_reconciliation_artifacts",
        } <= set(inspect(connection).get_table_names())
        migration.upgrade()
        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {"legacy_voice"}


class _CrashBeforeSecondTable:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.create_calls = 0

    def create_table(self, *args, **kwargs):
        self.create_calls += 1
        if self.create_calls == 2:
            raise RuntimeError("injected migration process crash")
        return self.delegate.create_table(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


def test_speech_reconciliation_migration_recovers_from_committed_partial_ddl(monkeypatch) -> None:
    """Model a backend where a process crash commits an earlier DDL step.

    The migration has no legacy data rewrite; recovery therefore consists of
    preserving the already-created job table and creating every missing child
    table/index on the next Alembic invocation.
    """

    db = create_engine("sqlite://")
    migration = importlib.import_module("migrations.versions.a2b3c4d5e6f7_add_speech_reconciliation_jobs")
    with db.connect() as connection:
        operations = Operations(MigrationContext.configure(connection))
        crashing = _CrashBeforeSecondTable(operations)
        monkeypatch.setattr(migration, "op", crashing)
        try:
            migration.upgrade()
        except RuntimeError as exc:
            assert str(exc) == "injected migration process crash"
        else:  # pragma: no cover - proves the fault injection remained active
            raise AssertionError("migration crash was not injected")
        connection.commit()
        assert "speech_reconciliation_jobs" in inspect(connection).get_table_names()
        assert "speech_reconciliation_attempts" not in inspect(connection).get_table_names()

        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()
        connection.commit()
        assert {
            "speech_reconciliation_jobs",
            "speech_reconciliation_attempts",
            "speech_reconciliation_budget_ledgers",
            "speech_reconciliation_checkpoints",
            "speech_reconciliation_artifacts",
        } <= set(inspect(connection).get_table_names())

        # Restart/replay is a no-op and downgrade remains complete after the
        # recovered partial execution.
        migration.upgrade()
        migration.downgrade()
        assert not {
            name
            for name in inspect(connection).get_table_names()
            if name.startswith("speech_reconciliation_")
        }


def test_speech_reconciliation_mutation_receipt_migration_is_additive_and_replay_safe(monkeypatch) -> None:
    db = create_engine("sqlite://")
    with db.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_voice (id VARCHAR PRIMARY KEY)"))
        connection.execute(
            sa.text("CREATE TABLE speech_reconciliation_jobs (id VARCHAR PRIMARY KEY)")
        )
        migration = importlib.import_module(
            "migrations.versions.4f9c2a7e1b6d_add_speech_reconciliation_mutations"
        )
        assert migration.down_revision == "ff4a5b6c7d8e"
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))

        migration.upgrade()
        migration.upgrade()
        inspector = inspect(connection)
        assert "speech_reconciliation_mutations" in inspector.get_table_names()
        assert {
            "tenant_id",
            "owner_subject",
            "job_id",
            "operation",
            "idempotency_key_digest",
            "request_digest",
            "result_job_version",
            "result_snapshot",
            "affected_attempt_id",
            "affected_fencing_epoch",
            "state_changed",
            "created_at_ms",
        } <= {
            column["name"]
            for column in inspector.get_columns("speech_reconciliation_mutations")
        }
        assert any(
            constraint.get("name") == "uq_speech_reconciliation_mutation_idempotency"
            and constraint.get("column_names")
            == [
                "tenant_id",
                "owner_subject",
                "job_id",
                "operation",
                "idempotency_key_digest",
            ]
            for constraint in inspector.get_unique_constraints(
                "speech_reconciliation_mutations"
            )
        )

        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {
            "legacy_voice",
            "speech_reconciliation_jobs",
        }
