from __future__ import annotations

from importlib import import_module

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowControlBindingDB,
    WorkflowControlCommandReceiptDB,
    WorkflowTransitionEffectDB,
    WorkflowTransitionOutboxDB,
)

_MIGRATION = "migrations.versions.d8f0a2c4e6b8_add_workflow_transition_outbox"
_BINDING_COLUMNS = {
    "active_transition_id",
    "last_transition_id",
    "last_transition_command_id",
    "last_transition_request_fingerprint",
    "last_transition_effect_fingerprint",
    "last_transition_outcome_fingerprint",
}
_RECEIPT_COLUMNS = {
    "request_fingerprint",
    "transition_id",
    "effect_fingerprint",
    "outcome_fingerprint",
    "dispatch_generation",
    "last_heartbeat_at",
}
_OUTBOX_COLUMNS = {
    "id",
    "tenant_id",
    "workflow_id",
    "run_id",
    "runtime_id",
    "kind",
    "request_payload",
    "command_id",
    "receipt_id",
    "request_fingerprint",
    "admitted_command_digest",
    "effect_fingerprint",
    "outcome_fingerprint",
    "expected_revision",
    "expected_checkpoint_ref",
    "result_status",
    "result_checkpoint_ref",
    "state",
    "available_at",
    "claim_owner",
    "claim_generation",
    "claim_expires_at",
    "last_heartbeat_at",
    "attempt_count",
    "last_error",
    "revision",
    "created_at",
    "updated_at",
    "completed_at",
}
_EFFECT_COLUMNS = {
    "id",
    "transition_id",
    "ordinal",
    "kind",
    "idempotency_key",
    "payload",
    "payload_digest",
    "state",
    "applied_generation",
    "result_payload",
    "result_digest",
    "revision",
    "created_at",
    "updated_at",
}


def _engine(path: str) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _create_c7_prerequisites(engine: sa.Engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "workflow_control_bindings",
        metadata,
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("legacy_value", sa.String(64), nullable=False),
    )
    sa.Table(
        "workflow_control_command_receipts",
        metadata,
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("legacy_value", sa.String(64), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO workflow_control_bindings (id, legacy_value) VALUES ('workflow-a', 'binding-legacy')")
        )
        connection.execute(
            sa.text(
                "INSERT INTO workflow_control_command_receipts (id, legacy_value) "
                "VALUES ('command-a', 'receipt-legacy')"
            )
        )


def _column_map(inspector: sa.Inspector, table_name: str) -> dict[str, dict]:
    return {value["name"]: value for value in inspector.get_columns(table_name)}


def _assert_model_parity(
    inspector: sa.Inspector,
    table_name: str,
    model_table: sa.Table,
) -> None:
    ddl_columns = _column_map(inspector, table_name)
    assert set(ddl_columns) == {column.name for column in model_table.columns}
    for model_column in model_table.columns:
        ddl_column = ddl_columns[model_column.name]
        assert ddl_column["nullable"] is model_column.nullable
        if isinstance(model_column.type, sa.String):
            assert isinstance(ddl_column["type"], sa.String)
            assert ddl_column["type"].length == model_column.type.length
        elif isinstance(model_column.type, sa.JSON):
            assert isinstance(ddl_column["type"], sa.JSON)
        elif isinstance(model_column.type, sa.BigInteger):
            assert isinstance(ddl_column["type"], sa.BigInteger)
        elif isinstance(model_column.type, sa.Integer):
            assert isinstance(ddl_column["type"], sa.Integer)
        elif isinstance(model_column.type, sa.Float):
            assert isinstance(ddl_column["type"], sa.Float)


def _outbox_values(
    *,
    transition_id: str,
    command_id: str,
    receipt_id: str,
) -> dict[str, object]:
    return {
        "id": transition_id,
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "runtime_id": "ananta-native",
        "kind": "command",
        "request_payload": '{"command":"advance"}',
        "command_id": command_id,
        "receipt_id": receipt_id,
        "request_fingerprint": "a" * 64,
        "effect_fingerprint": "b" * 64,
        "expected_revision": 7,
        "expected_checkpoint_ref": "checkpoint-7",
        "state": "ready",
        "available_at": 1_000.0,
        "created_at": 1_000.0,
        "updated_at": 1_000.0,
    }


_INSERT_OUTBOX = sa.text(
    "INSERT INTO workflow_transition_outbox "
    "(id, tenant_id, workflow_id, run_id, runtime_id, kind, request_payload, "
    "command_id, receipt_id, request_fingerprint, effect_fingerprint, "
    "expected_revision, expected_checkpoint_ref, state, available_at, "
    "created_at, updated_at) VALUES "
    "(:id, :tenant_id, :workflow_id, :run_id, :runtime_id, :kind, "
    ":request_payload, :command_id, :receipt_id, :request_fingerprint, "
    ":effect_fingerprint, :expected_revision, :expected_checkpoint_ref, "
    ":state, :available_at, :created_at, :updated_at)"
)


def test_transition_migration_is_additive_replay_safe_and_reversible(
    tmp_path,
    monkeypatch,
) -> None:
    engine = _engine(str(tmp_path / "transition-migration.db"))
    _create_c7_prerequisites(engine)
    migration = import_module(_MIGRATION)

    with engine.begin() as connection:
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        migration.upgrade()

        inspector = inspect(connection)
        assert {
            "workflow_control_bindings",
            "workflow_control_command_receipts",
            "workflow_transition_outbox",
            "workflow_transition_effects",
        } <= set(inspector.get_table_names())
        binding_columns = _column_map(inspector, "workflow_control_bindings")
        receipt_columns = _column_map(
            inspector,
            "workflow_control_command_receipts",
        )
        assert _BINDING_COLUMNS <= set(binding_columns)
        assert _RECEIPT_COLUMNS <= set(receipt_columns)
        assert all(binding_columns[name]["nullable"] is False for name in _BINDING_COLUMNS)
        assert all(receipt_columns[name]["nullable"] is False for name in _RECEIPT_COLUMNS)
        assert set(_column_map(inspector, "workflow_transition_outbox")) == _OUTBOX_COLUMNS
        assert set(_column_map(inspector, "workflow_transition_effects")) == _EFFECT_COLUMNS

        binding_defaults = connection.execute(
            sa.text(
                "SELECT active_transition_id, last_transition_id, "
                "last_transition_command_id, last_transition_request_fingerprint, "
                "last_transition_effect_fingerprint, "
                "last_transition_outcome_fingerprint "
                "FROM workflow_control_bindings WHERE id = 'workflow-a'"
            )
        ).one()
        receipt_defaults = connection.execute(
            sa.text(
                "SELECT request_fingerprint, transition_id, effect_fingerprint, "
                "outcome_fingerprint, dispatch_generation, last_heartbeat_at "
                "FROM workflow_control_command_receipts WHERE id = 'command-a'"
            )
        ).one()
        assert tuple(binding_defaults) == ("", "", "", "", "", "")
        assert tuple(receipt_defaults) == ("", "", "", "", 0, 0.0)

        assert {value["name"] for value in inspector.get_indexes("workflow_transition_outbox")} == {
            "ix_workflow_transition_due",
            "ix_workflow_transition_run_created",
            "ix_workflow_transition_workflow_state",
        }
        assert {value["name"] for value in inspector.get_indexes("workflow_transition_effects")} == {
            "ix_workflow_transition_effect_state"
        }
        assert {value["name"] for value in inspector.get_unique_constraints("workflow_transition_outbox")} == {
            "uq_workflow_transition_command",
            "uq_workflow_transition_receipt",
        }
        assert {value["name"] for value in inspector.get_unique_constraints("workflow_transition_effects")} == {
            "uq_workflow_transition_effect_key",
            "uq_workflow_transition_effect_ordinal",
        }
        assert {value["name"] for value in inspector.get_check_constraints("workflow_transition_outbox")} == {
            "ck_workflow_transition_non_negative"
        }
        assert {value["name"] for value in inspector.get_check_constraints("workflow_transition_effects")} == {
            "ck_workflow_transition_effect_non_negative"
        }
        foreign_keys = inspector.get_foreign_keys("workflow_transition_effects")
        assert foreign_keys == [
            {
                "name": "fk_workflow_transition_effect_transition",
                "constrained_columns": ["transition_id"],
                "referred_schema": None,
                "referred_table": "workflow_transition_outbox",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            }
        ]

        _assert_model_parity(
            inspector,
            "workflow_transition_outbox",
            WorkflowTransitionOutboxDB.__table__,
        )
        _assert_model_parity(
            inspector,
            "workflow_transition_effects",
            WorkflowTransitionEffectDB.__table__,
        )
        model_binding = WorkflowControlBindingDB.__table__.columns
        model_receipt = WorkflowControlCommandReceiptDB.__table__.columns
        for name in _BINDING_COLUMNS:
            assert binding_columns[name]["type"].length == model_binding[name].type.length
        for name in _RECEIPT_COLUMNS:
            if isinstance(model_receipt[name].type, sa.String):
                assert receipt_columns[name]["type"].length == model_receipt[name].type.length

        connection.execute(
            _INSERT_OUTBOX,
            _outbox_values(
                transition_id="transition-a",
                command_id="command-a",
                receipt_id="receipt-a",
            ),
        )
        stored_defaults = connection.execute(
            sa.text(
                "SELECT admitted_command_digest, outcome_fingerprint, "
                "result_status, result_checkpoint_ref, claim_owner, "
                "claim_generation, claim_expires_at, last_heartbeat_at, "
                "attempt_count, last_error, revision, completed_at "
                "FROM workflow_transition_outbox WHERE id = 'transition-a'"
            )
        ).one()
        assert tuple(stored_defaults) == (
            "",
            "",
            "{}",
            "",
            "",
            0,
            0.0,
            0.0,
            0,
            "",
            1,
            0.0,
        )
        connection.execute(
            sa.text(
                "INSERT INTO workflow_transition_effects "
                "(id, transition_id, ordinal, kind, idempotency_key, payload, "
                "payload_digest, state, created_at, updated_at) VALUES "
                "('effect-a', 'transition-a', 1, 'queue_reserve', 'task-a', "
                "'{}', :digest, 'planned', 1000, 1000)"
            ),
            {"digest": "c" * 64},
        )

        duplicate_cases = (
            _outbox_values(
                transition_id="transition-command-duplicate",
                command_id="command-a",
                receipt_id="receipt-b",
            ),
            _outbox_values(
                transition_id="transition-receipt-duplicate",
                command_id="command-b",
                receipt_id="receipt-a",
            ),
        )
        for values in duplicate_cases:
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(_INSERT_OUTBOX, values)
        negative_transition = _outbox_values(
            transition_id="transition-negative",
            command_id="command-negative",
            receipt_id="receipt-negative",
        )
        negative_transition["expected_revision"] = -1
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(_INSERT_OUTBOX, negative_transition)
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO workflow_transition_effects "
                    "(id, transition_id, ordinal, kind, idempotency_key, payload, "
                    "payload_digest, state, created_at, updated_at) VALUES "
                    "('effect-ordinal', 'transition-a', 1, 'queue_reserve', "
                    "'task-b', '{}', :digest, 'planned', 1000, 1000)"
                ),
                {"digest": "d" * 64},
            )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO workflow_transition_effects "
                    "(id, transition_id, ordinal, kind, idempotency_key, payload, "
                    "payload_digest, state, created_at, updated_at) VALUES "
                    "('effect-key', 'transition-a', 2, 'queue_reserve', "
                    "'task-a', '{}', :digest, 'planned', 1000, 1000)"
                ),
                {"digest": "e" * 64},
            )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO workflow_transition_effects "
                    "(id, transition_id, ordinal, kind, idempotency_key, payload, "
                    "payload_digest, state, created_at, updated_at) VALUES "
                    "('effect-orphan', 'missing', 1, 'queue_reserve', "
                    "'task-orphan', '{}', :digest, 'planned', 1000, 1000)"
                ),
                {"digest": "f" * 64},
            )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO workflow_transition_effects "
                    "(id, transition_id, ordinal, kind, idempotency_key, payload, "
                    "payload_digest, state, created_at, updated_at) VALUES "
                    "('effect-negative', 'transition-a', 0, 'queue_reserve', "
                    "'task-negative', '{}', :digest, 'planned', 1000, 1000)"
                ),
                {"digest": "0" * 64},
            )

        migration.downgrade()
        migration.downgrade()
        inspector = inspect(connection)
        assert "workflow_transition_outbox" not in inspector.get_table_names()
        assert "workflow_transition_effects" not in inspector.get_table_names()
        assert set(_column_map(inspector, "workflow_control_bindings")) == {
            "id",
            "legacy_value",
        }
        assert set(_column_map(inspector, "workflow_control_command_receipts")) == {"id", "legacy_value"}
        assert (
            connection.execute(
                sa.text("SELECT legacy_value FROM workflow_control_bindings WHERE id = 'workflow-a'")
            ).scalar_one()
            == "binding-legacy"
        )
        assert (
            connection.execute(
                sa.text("SELECT legacy_value FROM workflow_control_command_receipts WHERE id = 'command-a'")
            ).scalar_one()
            == "receipt-legacy"
        )

        migration.upgrade()
        assert {
            "workflow_transition_outbox",
            "workflow_transition_effects",
        } <= set(inspect(connection).get_table_names())


def test_transition_migration_fails_closed_without_c7_prerequisites(
    tmp_path,
    monkeypatch,
) -> None:
    engine = _engine(str(tmp_path / "missing-prerequisite.db"))
    migration = import_module(_MIGRATION)
    with engine.begin() as connection:
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        with pytest.raises(RuntimeError, match="prerequisite_missing"):
            migration.upgrade()
        assert inspect(connection).get_table_names() == []


def test_transition_migration_remains_on_the_single_head_chain() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_heads() == ["b9d1f3a5c7e0"]
    migration = scripts.get_revision("d8f0a2c4e6b8")
    assert migration is not None
    assert migration.down_revision == "c7e9a1b3d5f7"
    receipt_migration = scripts.get_revision("e9a2c4d6f8b0")
    assert receipt_migration is not None
    assert receipt_migration.down_revision == "d8f0a2c4e6b8"
    ownership_migration = scripts.get_revision("f0b2d4e6a8c1")
    assert ownership_migration is not None
    assert ownership_migration.down_revision == "e9a2c4d6f8b0"
    trace_migration = scripts.get_revision("a1c3e5f7b9d2")
    assert trace_migration is not None
    assert trace_migration.down_revision == "f0b2d4e6a8c1"
    queue_migration = scripts.get_revision("b2d4f6a8c0e3")
    assert queue_migration is not None
    assert queue_migration.down_revision == "a1c3e5f7b9d2"
    checkpoint_migration = scripts.get_revision("c3e5a7b9d1f4")
    assert checkpoint_migration is not None
    assert checkpoint_migration.down_revision == "b2d4f6a8c0e3"
