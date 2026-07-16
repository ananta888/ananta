from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "y3z4a5b6c7d8"

DATASET_INDEXES = {
    "ix_ml_intern_datasets_tenant_id",
    "ix_ml_intern_datasets_owner_subject",
    "ix_ml_intern_datasets_status",
    "ix_ml_intern_datasets_format_type",
    "ix_ml_intern_datasets_content_sha256",
    "ix_ml_intern_datasets_created_at",
    "ix_ml_intern_dataset_scope_created",
}
JOB_INDEXES = {
    "ix_ml_intern_training_jobs_tenant_id",
    "ix_ml_intern_training_jobs_owner_subject",
    "ix_ml_intern_training_jobs_task_id",
    "ix_ml_intern_training_jobs_dataset_id",
    "ix_ml_intern_training_jobs_job_type",
    "ix_ml_intern_training_jobs_mode",
    "ix_ml_intern_training_jobs_backend",
    "ix_ml_intern_training_jobs_base_model",
    "ix_ml_intern_training_jobs_status",
    "ix_ml_intern_training_jobs_phase",
    "ix_ml_intern_training_jobs_idempotency_key_digest",
    "ix_ml_intern_training_jobs_request_digest",
    "ix_ml_intern_training_jobs_worker_job_id",
    "ix_ml_intern_training_jobs_active_attempt_id",
    "ix_ml_intern_training_jobs_checkpoint_ref",
    "ix_ml_intern_training_jobs_result_ref",
    "ix_ml_intern_training_jobs_adapter_id",
    "ix_ml_intern_training_jobs_created_at",
    "ix_ml_intern_training_job_scope_created",
}
ATTEMPT_INDEXES = {
    "ix_ml_intern_training_attempts_job_id",
    "ix_ml_intern_training_attempts_tenant_id",
    "ix_ml_intern_training_attempts_owner_subject",
    "ix_ml_intern_training_attempts_status",
    "ix_ml_intern_training_attempts_fencing_token_digest",
    "ix_ml_intern_training_attempts_lease_expires_at",
    "ix_ml_intern_training_attempts_deadline_at",
    "ix_ml_intern_training_attempts_last_heartbeat_at",
    "ix_ml_intern_training_attempts_checkpoint_ref",
    "ix_ml_intern_training_attempts_result_ref",
}
EVENT_INDEXES = {
    "ix_ml_intern_training_events_job_id",
    "ix_ml_intern_training_events_tenant_id",
    "ix_ml_intern_training_events_owner_subject",
    "ix_ml_intern_training_events_sequence",
    "ix_ml_intern_training_events_event_type",
    "ix_ml_intern_training_events_created_at",
    "ix_ml_intern_event_scope_job",
}
CAPACITY_INDEXES = {"ix_ml_intern_training_capacity_leases_job_id"}
EXECUTION_INDEXES = {
    "ix_ml_intern_training_execution_leases_job_id",
    "ix_ml_intern_training_execution_leases_lease_expires_at",
}


def _alembic(database: Path, *arguments: str) -> None:
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database}",
        "ANANTA_DATA_DIR": str(database.parent / "data"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _names(items: list[dict[str, object]]) -> set[str | None]:
    return {item.get("name") for item in items}


def _assert_schema(database: Path) -> None:
    from agent.db_models.ml_intern_training import (
        MlInternDatasetDB,
        MlInternTrainingAttemptDB,
        MlInternTrainingCapacityLeaseDB,
        MlInternTrainingEventDB,
        MlInternTrainingExecutionLeaseDB,
        MlInternTrainingJobDB,
    )

    engine = sa.create_engine(f"sqlite:///{database}")
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    models = {
        "ml_intern_datasets": MlInternDatasetDB,
        "ml_intern_training_jobs": MlInternTrainingJobDB,
        "ml_intern_training_capacity_leases": MlInternTrainingCapacityLeaseDB,
        "ml_intern_training_execution_leases": MlInternTrainingExecutionLeaseDB,
        "ml_intern_training_attempts": MlInternTrainingAttemptDB,
        "ml_intern_training_events": MlInternTrainingEventDB,
    }
    assert set(models) <= tables
    for table_name, model in models.items():
        reflected = {item["name"]: item for item in inspector.get_columns(table_name)}
        declared = {column.name: column for column in model.__table__.columns}
        assert set(reflected) == set(declared)
        for column_name, column in declared.items():
            assert reflected[column_name]["nullable"] is column.nullable
            assert reflected[column_name]["type"].compile(dialect=engine.dialect) == column.type.compile(
                dialect=engine.dialect
            )

    assert "uq_ml_intern_dataset_scope_hash" in _names(inspector.get_unique_constraints("ml_intern_datasets"))
    assert "uq_ml_intern_training_job_scope_idempotency" in _names(
        inspector.get_unique_constraints("ml_intern_training_jobs")
    )
    assert "uq_ml_intern_attempt_job_number" in _names(inspector.get_unique_constraints("ml_intern_training_attempts"))
    event_uniques = _names(inspector.get_unique_constraints("ml_intern_training_events"))
    assert {
        "uq_ml_intern_event_job_sequence",
        "uq_ml_intern_event_job_dedupe",
    } <= event_uniques

    assert DATASET_INDEXES == _names(inspector.get_indexes("ml_intern_datasets"))
    assert JOB_INDEXES == _names(inspector.get_indexes("ml_intern_training_jobs"))
    assert ATTEMPT_INDEXES == _names(inspector.get_indexes("ml_intern_training_attempts"))
    assert EVENT_INDEXES == _names(inspector.get_indexes("ml_intern_training_events"))
    assert CAPACITY_INDEXES == _names(inspector.get_indexes("ml_intern_training_capacity_leases"))
    assert EXECUTION_INDEXES == _names(inspector.get_indexes("ml_intern_training_execution_leases"))

    job_foreign_keys = inspector.get_foreign_keys("ml_intern_training_jobs")
    assert any(item["referred_table"] == "ml_intern_datasets" for item in job_foreign_keys)
    for table_name in (
        "ml_intern_training_capacity_leases",
        "ml_intern_training_execution_leases",
        "ml_intern_training_attempts",
        "ml_intern_training_events",
    ):
        foreign_keys = inspector.get_foreign_keys(table_name)
        assert any(item["referred_table"] == "ml_intern_training_jobs" for item in foreign_keys)


def test_ml_intern_training_migration_up_down_and_reupgrade(tmp_path: Path) -> None:
    database = tmp_path / "ml-intern-training-migrations.db"

    _alembic(database, "upgrade", "head")
    _assert_schema(database)

    _alembic(database, "downgrade", PREVIOUS_REVISION)
    inspector = sa.inspect(sa.create_engine(f"sqlite:///{database}"))
    assert not {
        "ml_intern_datasets",
        "ml_intern_training_jobs",
        "ml_intern_training_capacity_leases",
        "ml_intern_training_execution_leases",
        "ml_intern_training_attempts",
        "ml_intern_training_events",
    }.intersection(inspector.get_table_names())

    _alembic(database, "upgrade", "head")
    _assert_schema(database)
