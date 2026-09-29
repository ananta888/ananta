from __future__ import annotations

from importlib import import_module

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowExecutionAttemptHistoryDB,
    WorkflowExecutionOwnershipDB,
    WorkflowRetryBudgetDB,
    WorkflowRetryConsumptionDB,
    WorkflowTransitionOwnershipReservationDB,
)

_MIGRATION = "migrations.versions.f0b2d4e6a8c1_add_workflow_transition_ownership_reservations"
_TABLE = "workflow_transition_ownership_reservations"
_PREREQUISITE_TABLES = (
    WorkflowExecutionOwnershipDB.__table__,
    WorkflowExecutionAttemptHistoryDB.__table__,
    WorkflowRetryBudgetDB.__table__,
    WorkflowRetryConsumptionDB.__table__,
)


class _SyntheticIndexInspector:
    def __init__(self, index: dict[str, object]) -> None:
        self._index = index

    def get_unique_constraints(self, table_name: str) -> list[dict[str, object]]:
        assert table_name == _TABLE
        return [
            {
                "name": "uq_workflow_transition_ownership_res_effect",
                "column_names": ["effect_id"],
            }
        ]

    def get_pk_constraint(self, table_name: str) -> dict[str, object]:
        assert table_name == _TABLE
        return {
            "name": "pk_workflow_transition_ownership_reservations",
            "constrained_columns": ["receipt_id"],
        }

    def get_indexes(self, table_name: str) -> list[dict[str, object]]:
        assert table_name == _TABLE
        return [self._index]


def _engine(path: str) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{path}")


def _create_prerequisites(
    connection: sa.Connection,
    *,
    omit: str = "",
) -> None:
    for table in _PREREQUISITE_TABLES:
        if table.name != omit:
            table.create(connection)


def _model_parity(inspector: sa.Inspector) -> None:
    actual = {value["name"]: value for value in inspector.get_columns(_TABLE)}
    expected = WorkflowTransitionOwnershipReservationDB.__table__.columns
    assert set(actual) == {column.name for column in expected}
    for column in expected:
        reflected = actual[column.name]
        assert reflected["nullable"] is column.nullable
        if isinstance(column.type, sa.String):
            assert isinstance(reflected["type"], sa.String)
            assert reflected["type"].length == column.type.length
        elif isinstance(column.type, sa.BigInteger):
            assert isinstance(reflected["type"], sa.BigInteger)
        elif isinstance(column.type, sa.Integer):
            assert isinstance(reflected["type"], sa.Integer)
            assert not isinstance(reflected["type"], sa.BigInteger)
        elif isinstance(column.type, sa.Boolean):
            assert isinstance(reflected["type"], sa.Boolean)
        elif isinstance(column.type, sa.Float):
            assert isinstance(reflected["type"], sa.Float)
        elif isinstance(column.type, sa.JSON):
            assert isinstance(reflected["type"], sa.JSON)


def _reservation_row(
    *,
    receipt_id: str,
    effect_id: str,
    operation_fence_id: str,
    attempt_id: str,
    acquired_revision: int,
    acquired_fencing_token: int,
    step_id: str = "step-a",
) -> dict[str, object]:
    return {
        "receipt_id": receipt_id,
        "transition_id": f"transition-{receipt_id}",
        "effect_id": effect_id,
        "operation_fence_id": operation_fence_id,
        "attempt_id": attempt_id,
        "owner_id": f"owner-{receipt_id}",
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "runtime_id": "ananta-native",
        "step_id": step_id,
        "ownership_intent_digest": "a" * 64,
        "acquisition_record_digest": "b" * 64,
        "receipt_digest": "c" * 64,
        "creator_claim_generation": 1,
        "acquired_revision": acquired_revision,
        "acquired_fencing_token": acquired_fencing_token,
        "maximum_retries": 3,
        "retry_consumed": acquired_revision > 1,
        "planned_at": 1_000.0,
        "reserved_at": 1_001.0,
        "lease_expires_at": 1_011.0,
        "receipt": {},
    }


def _insert_existing_prerequisite_state(connection: sa.Connection) -> None:
    ownership = {
        "schema": "ananta.execution_ownership.v1",
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "step_id": "step-a",
        "attempt_id": "attempt-existing",
        "owner_id": "owner-existing",
        "fencing_token": 1,
        "revision": 1,
        "status": "active",
        "lease_expires_at": 1_010.0,
        "last_heartbeat_at": 1_000.0,
        "result_ack_key": "",
        "failure_code": "",
    }
    connection.execute(
        sa.insert(WorkflowExecutionOwnershipDB.__table__).values(
            id="ownership-existing",
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            step_id="step-a",
            attempt_id="attempt-existing",
            owner_id="owner-existing",
            status="active",
            revision=1,
            fencing_token=1,
            lease_expires_at=1_010.0,
            last_heartbeat_at=1_000.0,
            ownership=ownership,
        )
    )
    connection.execute(
        sa.insert(WorkflowExecutionAttemptHistoryDB.__table__).values(
            id="history-existing",
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            step_id="step-a",
            attempt_id="attempt-existing",
            owner_id="owner-existing",
            status="active",
            revision=1,
            fencing_token=1,
            recorded_at=1_000.0,
            ownership=ownership,
        )
    )
    connection.execute(
        sa.insert(WorkflowRetryBudgetDB.__table__).values(
            id="budget-existing",
            tenant_id="tenant-a",
            run_id="run-a",
            used=1,
            maximum=3,
            revision=1,
            updated_at=999.0,
        )
    )
    connection.execute(
        sa.insert(WorkflowRetryConsumptionDB.__table__).values(
            id="consumption-existing",
            tenant_id="tenant-a",
            run_id="run-a",
            retry_id="retry-existing",
            category="provider",
            consumed_at=999.0,
        )
    )


def _prerequisite_snapshot(connection: sa.Connection) -> dict[str, tuple[object, ...]]:
    return {
        table.name: tuple(connection.execute(sa.select(table).order_by(*table.primary_key.columns)).mappings())
        for table in _PREREQUISITE_TABLES
    }


def _sqlite_table_snapshot(
    connection: sa.Connection,
    table: str,
) -> tuple[tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...]]:
    schema = connection.exec_driver_sql(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name = ? OR name = ? ORDER BY type, name",
        (table, table),
    ).fetchall()
    rows = connection.exec_driver_sql(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
    return tuple(tuple(row) for row in schema), tuple(tuple(row) for row in rows)


def test_ownership_reservation_migration_is_additive_replay_safe_and_reversible(
    tmp_path,
    monkeypatch,
) -> None:
    engine = _engine(str(tmp_path / "ownership-reservations.sqlite"))
    migration = import_module(_MIGRATION)
    assert migration.revision == "f0b2d4e6a8c1"
    assert migration.down_revision == "e9a2c4d6f8b0"

    with engine.begin() as connection:
        _create_prerequisites(connection)
        _insert_existing_prerequisite_state(connection)
        before = _prerequisite_snapshot(connection)
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
            "uq_workflow_transition_ownership_res_effect": ("effect_id",),
            "uq_workflow_transition_ownership_res_fence": ("operation_fence_id",),
            "uq_workflow_transition_ownership_res_attempt": ("attempt_id",),
            "uq_workflow_transition_ownership_res_revision": (
                "tenant_id",
                "run_id",
                "step_id",
                "acquired_revision",
            ),
            "uq_workflow_transition_ownership_res_current_fence": (
                "tenant_id",
                "run_id",
                "step_id",
                "acquired_fencing_token",
            ),
        }
        assert {value["name"]: tuple(value["column_names"]) for value in inspector.get_indexes(_TABLE)} == {
            "ix_workflow_transition_ownership_res_transition": ("transition_id",),
            "ix_workflow_transition_ownership_res_tenant_run": (
                "tenant_id",
                "run_id",
            ),
            "ix_workflow_transition_ownership_res_scope": (
                "tenant_id",
                "run_id",
                "step_id",
            ),
            "ix_workflow_transition_ownership_res_owner": ("owner_id",),
        }
        assert {value["name"] for value in inspector.get_check_constraints(_TABLE)} == {
            "ck_workflow_transition_ownership_res_valid"
        }
        assert connection.execute(sa.text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar_one() == 0
        assert _prerequisite_snapshot(connection) == before

        table = WorkflowTransitionOwnershipReservationDB.__table__
        connection.execute(
            sa.insert(table).values(
                **_reservation_row(
                    receipt_id="receipt-a",
                    effect_id="effect-a",
                    operation_fence_id="fence-a",
                    attempt_id="attempt-a",
                    acquired_revision=2,
                    acquired_fencing_token=2,
                )
            )
        )
        connection.execute(
            sa.insert(table).values(
                **_reservation_row(
                    receipt_id="receipt-b",
                    effect_id="effect-b",
                    operation_fence_id="fence-b",
                    attempt_id="attempt-b",
                    acquired_revision=3,
                    acquired_fencing_token=3,
                )
            )
        )
        connection.execute(
            sa.insert(table).values(
                **_reservation_row(
                    receipt_id="receipt-max-counter",
                    effect_id="effect-max-counter",
                    operation_fence_id="fence-max-counter",
                    attempt_id="attempt-max-counter",
                    acquired_revision=2_147_483_647,
                    acquired_fencing_token=2_147_483_647,
                    step_id="step-max-counter",
                )
            )
        )
        conflicts = (
            _reservation_row(
                receipt_id="receipt-effect-conflict",
                effect_id="effect-a",
                operation_fence_id="fence-c",
                attempt_id="attempt-c",
                acquired_revision=4,
                acquired_fencing_token=4,
            ),
            _reservation_row(
                receipt_id="receipt-fence-conflict",
                effect_id="effect-c",
                operation_fence_id="fence-a",
                attempt_id="attempt-d",
                acquired_revision=4,
                acquired_fencing_token=4,
            ),
            _reservation_row(
                receipt_id="receipt-attempt-conflict",
                effect_id="effect-d",
                operation_fence_id="fence-d",
                attempt_id="attempt-a",
                acquired_revision=4,
                acquired_fencing_token=4,
            ),
            _reservation_row(
                receipt_id="receipt-revision-conflict",
                effect_id="effect-e",
                operation_fence_id="fence-e",
                attempt_id="attempt-e",
                acquired_revision=2,
                acquired_fencing_token=4,
            ),
            _reservation_row(
                receipt_id="receipt-current-fence-conflict",
                effect_id="effect-f",
                operation_fence_id="fence-f",
                attempt_id="attempt-f",
                acquired_revision=4,
                acquired_fencing_token=2,
            ),
        )
        for values in conflicts:
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(sa.insert(table).values(**values))

        invalid_changes = (
            {"creator_claim_generation": 0},
            {"acquired_revision": 0},
            {"acquired_revision": 2_147_483_648},
            {"acquired_fencing_token": 0},
            {"acquired_fencing_token": 2_147_483_648},
            {"maximum_retries": -1},
            {"maximum_retries": 2_147_483_648},
            {"retry_consumed": sa.literal_column("2")},
            {"planned_at": 0.0},
            {"reserved_at": 999.0},
            {"lease_expires_at": 1_001.0},
        )
        for index, changes in enumerate(invalid_changes, start=10):
            invalid = _reservation_row(
                receipt_id=f"receipt-invalid-{index}",
                effect_id=f"effect-invalid-{index}",
                operation_fence_id=f"fence-invalid-{index}",
                attempt_id=f"attempt-invalid-{index}",
                acquired_revision=index,
                acquired_fencing_token=index,
                step_id=f"step-invalid-{index}",
            )
            invalid.update(changes)
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(sa.insert(table).values(**invalid))

        migration.downgrade()
        migration.downgrade()
        assert _TABLE not in inspect(connection).get_table_names()
        assert _prerequisite_snapshot(connection) == before


@pytest.mark.parametrize(
    "missing_table",
    [table.name for table in _PREREQUISITE_TABLES],
)
def test_ownership_reservation_migration_requires_every_prerequisite(
    tmp_path,
    monkeypatch,
    missing_table: str,
) -> None:
    engine = _engine(str(tmp_path / f"missing-{missing_table}.sqlite"))
    migration = import_module(_MIGRATION)
    with engine.begin() as connection:
        _create_prerequisites(connection, omit=missing_table)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        with pytest.raises(RuntimeError, match="prerequisite_missing"):
            migration.upgrade()
        assert _TABLE not in inspect(connection).get_table_names()


def test_ownership_reservation_migration_rejects_compact_sqlite_prerequisite(
    tmp_path,
    monkeypatch,
) -> None:
    engine = _engine(str(tmp_path / "compact-prerequisite.sqlite"))
    migration = import_module(_MIGRATION)
    with engine.begin() as connection:
        _create_prerequisites(
            connection,
            omit="workflow_execution_ownership",
        )
        connection.execute(
            sa.text(
                "CREATE TABLE workflow_execution_ownership ("
                "tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, "
                "step_id TEXT NOT NULL, revision INTEGER NOT NULL, "
                "fencing_token INTEGER NOT NULL, ownership_json TEXT NOT NULL, "
                "PRIMARY KEY (tenant_id, run_id, step_id))"
            )
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        with pytest.raises(RuntimeError, match="prerequisite_conflict"):
            migration.upgrade()
        assert _TABLE not in inspect(connection).get_table_names()


def test_ownership_reservation_migration_rejects_wrong_prerequisite_string_length(
    tmp_path,
    monkeypatch,
) -> None:
    engine = _engine(str(tmp_path / "wrong-prerequisite-length.sqlite"))
    migration = import_module(_MIGRATION)
    with engine.begin() as connection:
        _create_prerequisites(
            connection,
            omit="workflow_execution_ownership",
        )
        metadata = sa.MetaData()
        ownership = WorkflowExecutionOwnershipDB.__table__.to_metadata(metadata)
        ownership.c.owner_id.type = sa.String(32)
        ownership.create(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        with pytest.raises(RuntimeError, match="prerequisite_conflict"):
            migration.upgrade()
        assert _TABLE not in inspect(connection).get_table_names()


def test_ownership_reservation_migration_rejects_target_drift_and_foreign_keys(
    tmp_path,
    monkeypatch,
) -> None:
    migration = import_module(_MIGRATION)

    drifted_engine = _engine(str(tmp_path / "drifted-target.sqlite"))
    with drifted_engine.begin() as connection:
        _create_prerequisites(connection)
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

    foreign_key_engine = _engine(str(tmp_path / "foreign-key-target.sqlite"))
    with foreign_key_engine.begin() as connection:
        _create_prerequisites(connection)
        metadata = sa.MetaData()
        target = WorkflowTransitionOwnershipReservationDB.__table__.to_metadata(metadata)
        target.append_constraint(
            sa.ForeignKeyConstraint(
                ["receipt_id"],
                [f"{_TABLE}.effect_id"],
                name="fk_workflow_transition_ownership_res_drift",
            )
        )
        target.create(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        with pytest.raises(RuntimeError, match="schema_conflict"):
            migration.upgrade()
        assert inspect(connection).get_foreign_keys(_TABLE)


def test_ownership_reservation_migration_downgrade_preserves_drifted_same_name_table(
    tmp_path,
    monkeypatch,
) -> None:
    engine = _engine(str(tmp_path / "downgrade-drifted-target.sqlite"))
    migration = import_module(_MIGRATION)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f'CREATE TABLE "{_TABLE}" (receipt_id TEXT NOT NULL PRIMARY KEY, payload BLOB NOT NULL)'
        )
        connection.exec_driver_sql(
            f'INSERT INTO "{_TABLE}" (receipt_id, payload) VALUES (?, ?)',
            ("foreign-receipt", b"\x00\xffforeign-payload"),
        )
        before = _sqlite_table_snapshot(connection, _TABLE)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        with pytest.raises(RuntimeError, match="schema_conflict"):
            migration.downgrade()
        assert _TABLE in inspect(connection).get_table_names()
        assert _sqlite_table_snapshot(connection, _TABLE) == before


@pytest.mark.parametrize(
    ("column_name", "wrong_type"),
    (
        ("runtime_id", sa.CHAR(64)),
        ("creator_claim_generation", sa.Integer()),
        ("maximum_retries", sa.SmallInteger()),
        ("retry_consumed", sa.Integer()),
        ("planned_at", sa.REAL()),
        ("receipt", sa.Text()),
    ),
)
def test_ownership_reservation_migration_rejects_wrong_target_type_without_mutation(
    tmp_path,
    monkeypatch,
    column_name: str,
    wrong_type: sa.types.TypeEngine,
) -> None:
    engine = _engine(str(tmp_path / f"wrong-target-type-{column_name}.sqlite"))
    migration = import_module(_MIGRATION)
    with engine.begin() as connection:
        _create_prerequisites(connection)
        metadata = sa.MetaData()
        target = WorkflowTransitionOwnershipReservationDB.__table__.to_metadata(metadata)
        target.c[column_name].type = wrong_type
        target.create(connection)
        connection.execute(sa.text("DROP INDEX ix_workflow_transition_ownership_res_owner"))
        before = _sqlite_table_snapshot(connection, _TABLE)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        with pytest.raises(RuntimeError, match="schema_conflict"):
            migration.upgrade()
        assert _sqlite_table_snapshot(connection, _TABLE) == before


@pytest.mark.parametrize(
    "drift",
    ("wrong_columns", "wrong_uniqueness", "unknown_extra", "partial"),
)
def test_ownership_reservation_migration_rejects_index_drift_without_mutation(
    tmp_path,
    monkeypatch,
    drift: str,
) -> None:
    engine = _engine(str(tmp_path / f"index-drift-{drift}.sqlite"))
    migration = import_module(_MIGRATION)
    with engine.begin() as connection:
        _create_prerequisites(connection)
        metadata = sa.MetaData()
        WorkflowTransitionOwnershipReservationDB.__table__.to_metadata(metadata).create(connection)
        connection.execute(sa.text("DROP INDEX ix_workflow_transition_ownership_res_owner"))
        if drift == "wrong_columns":
            connection.execute(sa.text(f"CREATE INDEX ix_workflow_transition_ownership_res_owner ON {_TABLE} (run_id)"))
        elif drift == "wrong_uniqueness":
            connection.execute(
                sa.text(f"CREATE UNIQUE INDEX ix_workflow_transition_ownership_res_owner ON {_TABLE} (owner_id)")
            )
        elif drift == "unknown_extra":
            connection.execute(
                sa.text(f"CREATE INDEX ix_workflow_transition_ownership_res_unexpected ON {_TABLE} (owner_id)")
            )
        else:
            connection.execute(
                sa.text(
                    f"CREATE INDEX ix_workflow_transition_ownership_res_owner "
                    f"ON {_TABLE} (owner_id) WHERE owner_id <> ''"
                )
            )
        before = _sqlite_table_snapshot(connection, _TABLE)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        with pytest.raises(RuntimeError, match="schema_conflict"):
            migration.upgrade()
        assert _sqlite_table_snapshot(connection, _TABLE) == before


def test_ownership_reservation_migration_normalizes_postgresql_check_reflection() -> None:
    migration = import_module(_MIGRATION)
    reflected = (
        "CHECK (((creator_claim_generation > (0)::bigint) "
        "AND (acquired_revision > (0)::bigint) "
        "AND (acquired_revision <= (2147483647)::bigint) "
        "AND (acquired_fencing_token > (0)::bigint) "
        "AND (acquired_fencing_token <= (2147483647)::bigint) "
        "AND (maximum_retries >= (0)::integer) "
        "AND (maximum_retries <= 2147483647::integer) "
        "AND ((retry_consumed = false) OR (retry_consumed = true)) "
        "AND (planned_at > (0)::double precision) "
        "AND (reserved_at >= planned_at) "
        "AND (lease_expires_at > reserved_at)))"
    )
    assert migration._normalized_check_expression(reflected) == migration._normalized_check_expression(
        migration._CHECK_SQL
    )


@pytest.mark.parametrize(
    ("metadata", "allow_include", "allow_nulls_distinct"),
    (
        ({"dialect_options": {"not_valid": True}}, False, False),
        (
            {"dialect_options": {"postgresql_nulls_not_distinct": True}},
            True,
            True,
        ),
        (
            {"dialect_options": {"postgresql_include": ["run_id"]}},
            True,
            False,
        ),
        (
            {"dialect_options": {"postgresql_ops": {"owner_id": "text_pattern_ops"}}},
            True,
            False,
        ),
    ),
)
def test_ownership_reservation_migration_rejects_nondefault_reflection_options(
    metadata: dict[str, object],
    allow_include: bool,
    allow_nulls_distinct: bool,
) -> None:
    migration = import_module(_MIGRATION)
    assert migration._has_nondefault_dialect_options(
        metadata,
        allow_empty_postgresql_include=allow_include,
        allow_postgresql_nulls_distinct=allow_nulls_distinct,
    )


def test_ownership_reservation_migration_accepts_empty_postgresql_reflection_defaults() -> None:
    migration = import_module(_MIGRATION)
    metadata = {
        "dialect_options": {
            "postgresql_include": [],
            "postgresql_nulls_not_distinct": False,
        }
    }
    assert not migration._has_nondefault_dialect_options(
        metadata,
        allow_empty_postgresql_include=True,
        allow_postgresql_nulls_distinct=True,
    )


@pytest.mark.parametrize(
    "drift",
    (
        {"expressions": ["lower(owner_id)"]},
        {"column_sorting": {"owner_id": ("desc",)}},
        {
            "include_columns": ["run_id"],
            "dialect_options": {"postgresql_include": ["run_id"]},
        },
        {"dialect_options": {"postgresql_where": "owner_id IS NOT NULL"}},
        {"dialect_options": {"postgresql_using": "hash"}},
    ),
)
def test_ownership_reservation_migration_rejects_nondefault_index_reflection(
    drift: dict[str, object],
) -> None:
    migration = import_module(_MIGRATION)
    metadata: dict[str, object] = {
        "name": "ix_workflow_transition_ownership_res_owner",
        "column_names": ["owner_id"],
        "unique": False,
        "include_columns": [],
        "dialect_options": {"postgresql_include": []},
    }
    metadata.update(drift)
    assert not migration._target_index_shape_matches(
        metadata,
        "ix_workflow_transition_ownership_res_owner",
    )


@pytest.mark.parametrize(
    "drift",
    (
        {"column_names": ["owner_id"]},
        {"unique": False},
        {
            "include_columns": ["run_id"],
            "dialect_options": {"postgresql_include": ["run_id"]},
        },
        {"dialect_options": {"postgresql_with": {"fillfactor": "70"}}},
    ),
)
def test_ownership_reservation_migration_rejects_drifted_constraint_index_reflection(
    drift: dict[str, object],
) -> None:
    migration = import_module(_MIGRATION)
    constraint_name = "uq_workflow_transition_ownership_res_effect"
    metadata: dict[str, object] = {
        "name": constraint_name,
        "duplicates_constraint": constraint_name,
        "column_names": ["effect_id"],
        "unique": True,
        "include_columns": [],
        "dialect_options": {
            "postgresql_include": [],
            "postgresql_nulls_not_distinct": False,
        },
    }
    metadata.update(drift)
    with pytest.raises(
        RuntimeError,
        match="workflow_transition_ownership_reservation_schema_conflict",
    ):
        migration._validated_target_indexes(_SyntheticIndexInspector(metadata))


def test_ownership_reservation_migration_accepts_default_constraint_index_reflection() -> None:
    migration = import_module(_MIGRATION)
    constraint_name = "uq_workflow_transition_ownership_res_effect"
    metadata = {
        "name": constraint_name,
        "duplicates_constraint": constraint_name,
        "column_names": ["effect_id"],
        "unique": True,
        "include_columns": [],
        "dialect_options": {
            "postgresql_include": [],
            "postgresql_nulls_not_distinct": False,
        },
    }
    assert migration._validated_target_indexes(_SyntheticIndexInspector(metadata)) == {}


@pytest.mark.parametrize("drift", ("weakened_comparison", "ungrouped_boolean"))
def test_ownership_reservation_migration_rejects_same_name_weakened_check_without_mutation(
    tmp_path,
    monkeypatch,
    drift: str,
) -> None:
    engine = _engine(str(tmp_path / f"weakened-check-{drift}.sqlite"))
    migration = import_module(_MIGRATION)
    with engine.begin() as connection:
        _create_prerequisites(connection)
        metadata = sa.MetaData()
        target = WorkflowTransitionOwnershipReservationDB.__table__.to_metadata(metadata)
        for constraint in tuple(target.constraints):
            if isinstance(constraint, sa.CheckConstraint):
                target.constraints.remove(constraint)
        valid_check = (
            "creator_claim_generation > 0 "
            "AND acquired_revision > 0 "
            "AND acquired_revision <= 2147483647 "
            "AND acquired_fencing_token > 0 "
            "AND acquired_fencing_token <= 2147483647 "
            "AND maximum_retries >= 0 "
            "AND maximum_retries <= 2147483647 "
            "AND (retry_consumed = FALSE OR retry_consumed = TRUE) "
            "AND planned_at > 0 "
            "AND reserved_at >= planned_at "
            "AND lease_expires_at > reserved_at"
        )
        if drift == "weakened_comparison":
            drifted_check = valid_check.replace("creator_claim_generation > 0", "creator_claim_generation >= 0")
        else:
            drifted_check = valid_check.replace(
                "(retry_consumed = FALSE OR retry_consumed = TRUE)",
                "retry_consumed = FALSE OR retry_consumed = TRUE",
            )
        assert drifted_check != valid_check
        target.append_constraint(
            sa.CheckConstraint(
                drifted_check,
                name="ck_workflow_transition_ownership_res_valid",
            )
        )
        target.create(connection)
        before = _sqlite_table_snapshot(connection, _TABLE)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        with pytest.raises(RuntimeError, match="schema_conflict"):
            migration.upgrade()
        assert _sqlite_table_snapshot(connection, _TABLE) == before


def test_ownership_reservation_migration_rejects_foreign_index_name_before_ddl(
    tmp_path,
    monkeypatch,
) -> None:
    engine = _engine(str(tmp_path / "foreign-index-name.sqlite"))
    migration = import_module(_MIGRATION)
    with engine.begin() as connection:
        _create_prerequisites(connection)
        connection.execute(sa.text("CREATE TABLE unrelated_receipts (owner_id VARCHAR(256) NOT NULL)"))
        connection.execute(
            sa.text("CREATE INDEX ix_workflow_transition_ownership_res_owner ON unrelated_receipts (owner_id)")
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        with pytest.raises(RuntimeError, match="schema_conflict"):
            migration.upgrade()
        assert _TABLE not in inspect(connection).get_table_names()
