from __future__ import annotations

from importlib import import_module

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowSideEffectLedgerDB,
    WorkflowTransitionSideEffectAuthorizationDB,
)

_MIGRATION = "migrations.versions.e9a2c4d6f8b0_add_workflow_transition_side_effect_authorizations"
_TABLE = "workflow_transition_side_effect_authorizations"


def _engine(path: str) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{path}")


def _model_parity(inspector: sa.Inspector) -> None:
    actual = {value["name"]: value for value in inspector.get_columns(_TABLE)}
    expected = WorkflowTransitionSideEffectAuthorizationDB.__table__.columns
    assert set(actual) == {column.name for column in expected}
    for column in expected:
        reflected = actual[column.name]
        assert reflected["nullable"] is column.nullable
        if isinstance(column.type, sa.String):
            assert isinstance(reflected["type"], sa.String)
            assert reflected["type"].length == column.type.length
        elif isinstance(column.type, sa.BigInteger):
            assert isinstance(reflected["type"], sa.BigInteger)
        elif isinstance(column.type, sa.Float):
            assert isinstance(reflected["type"], sa.Float)
        elif isinstance(column.type, sa.JSON):
            assert isinstance(reflected["type"], sa.JSON)


def _row(
    *,
    receipt_id: str,
    effect_id: str,
    operation_fence_id: str,
    authorized_revision: int,
) -> dict[str, object]:
    return {
        "receipt_id": receipt_id,
        "transition_id": f"transition-{receipt_id}",
        "effect_id": effect_id,
        "operation_id": "operation-a",
        "operation_fence_id": operation_fence_id,
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "runtime_id": "ananta-native",
        "step_id": "step-a",
        "operation_intent_digest": "a" * 64,
        "authorization_envelope_id": "envelope-a",
        "authorization_envelope_digest": "b" * 64,
        "ownership_attempt_id": f"ownership-{receipt_id}",
        "ownership_fencing_token": authorized_revision,
        "creator_claim_generation": 1,
        "authorized_ledger_revision": authorized_revision,
        "planned_at": 1_000.0,
        "authorized_at": 1_000.0,
        "receipt_digest": "c" * 64,
        "receipt": {},
    }


def test_authorization_receipt_migration_is_additive_replay_safe_and_reversible(
    tmp_path,
    monkeypatch,
) -> None:
    engine = _engine(str(tmp_path / "authorization-receipts.sqlite"))
    migration = import_module(_MIGRATION)
    with engine.begin() as connection:
        WorkflowSideEffectLedgerDB.__table__.create(connection)
        connection.execute(
            sa.insert(WorkflowSideEffectLedgerDB.__table__).values(
                operation_id="operation-existing",
                tenant_id="tenant-a",
                workflow_id="workflow-a",
                run_id="run-a",
                step_id="step-a",
                status="completed",
                revision=7,
                fencing_token=3,
                updated_at=999.0,
                record={},
            )
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()
        migration.upgrade()

        inspector = inspect(connection)
        assert _TABLE in inspector.get_table_names()
        _model_parity(inspector)
        assert inspector.get_foreign_keys(_TABLE) == []
        assert {value["name"]: tuple(value["column_names"]) for value in inspector.get_unique_constraints(_TABLE)} == {
            "uq_workflow_transition_side_effect_auth_effect": ("effect_id",),
            "uq_workflow_transition_side_effect_auth_fence": ("operation_fence_id",),
            "uq_workflow_transition_side_effect_auth_revision": (
                "operation_id",
                "authorized_ledger_revision",
            ),
        }
        assert {value["name"]: tuple(value["column_names"]) for value in inspector.get_indexes(_TABLE)} == {
            "ix_workflow_transition_side_effect_auth_operation": ("operation_id",),
            "ix_workflow_transition_side_effect_auth_tenant_run": (
                "tenant_id",
                "run_id",
            ),
            "ix_workflow_transition_side_effect_auth_transition": ("transition_id",),
        }
        assert {value["name"] for value in inspector.get_check_constraints(_TABLE)} == {
            "ck_workflow_transition_side_effect_auth_positive"
        }
        assert connection.execute(sa.text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar_one() == 0
        assert (
            connection.execute(
                sa.text("SELECT revision FROM workflow_side_effect_ledger WHERE operation_id = 'operation-existing'")
            ).scalar_one()
            == 7
        )

        table = WorkflowTransitionSideEffectAuthorizationDB.__table__
        connection.execute(
            sa.insert(table).values(
                **_row(
                    receipt_id="receipt-a",
                    effect_id="effect-a",
                    operation_fence_id="fence-a",
                    authorized_revision=2,
                )
            )
        )
        connection.execute(
            sa.insert(table).values(
                **_row(
                    receipt_id="receipt-b",
                    effect_id="effect-b",
                    operation_fence_id="fence-b",
                    authorized_revision=3,
                )
            )
        )
        for values in (
            _row(
                receipt_id="receipt-effect-conflict",
                effect_id="effect-a",
                operation_fence_id="fence-c",
                authorized_revision=4,
            ),
            _row(
                receipt_id="receipt-fence-conflict",
                effect_id="effect-c",
                operation_fence_id="fence-a",
                authorized_revision=4,
            ),
            _row(
                receipt_id="receipt-revision-conflict",
                effect_id="effect-c",
                operation_fence_id="fence-c",
                authorized_revision=2,
            ),
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(sa.insert(table).values(**values))
        invalid = _row(
            receipt_id="receipt-invalid",
            effect_id="effect-invalid",
            operation_fence_id="fence-invalid",
            authorized_revision=1,
        )
        invalid["ownership_fencing_token"] = 0
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(sa.insert(table).values(**invalid))

        migration.downgrade()
        migration.downgrade()
        assert _TABLE not in inspect(connection).get_table_names()
        assert "workflow_side_effect_ledger" in inspect(connection).get_table_names()
        assert (
            connection.execute(
                sa.text("SELECT revision FROM workflow_side_effect_ledger WHERE operation_id = 'operation-existing'")
            ).scalar_one()
            == 7
        )


def test_authorization_receipt_migration_fails_closed_on_missing_or_drifted_schema(
    tmp_path,
    monkeypatch,
) -> None:
    migration = import_module(_MIGRATION)
    missing_engine = _engine(str(tmp_path / "missing.sqlite"))
    with missing_engine.begin() as connection:
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        with pytest.raises(RuntimeError, match="prerequisite_missing"):
            migration.upgrade()
        assert inspect(connection).get_table_names() == []

    drifted_engine = _engine(str(tmp_path / "drifted.sqlite"))
    with drifted_engine.begin() as connection:
        WorkflowSideEffectLedgerDB.__table__.create(connection)
        connection.execute(
            sa.text(f"CREATE TABLE {_TABLE} (receipt_id VARCHAR(256) PRIMARY KEY, effect_id VARCHAR(256) NOT NULL)")
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        with pytest.raises(RuntimeError, match="schema_conflict"):
            migration.upgrade()
        assert {value["name"] for value in inspect(connection).get_columns(_TABLE)} == {"receipt_id", "effect_id"}
