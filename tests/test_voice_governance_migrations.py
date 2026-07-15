from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
VOICE_PRE_TTL_REVISION = "m1n2o3p4q5r6"


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


def _voice_schema(database: Path) -> tuple[set[str | None], set[str], set[str | None]]:
    inspector = sa.inspect(sa.create_engine(f"sqlite:///{database}"))
    feedback_constraints = {item.get("name") for item in inspector.get_unique_constraints("voice_feedback")}
    idempotency_columns = {item["name"] for item in inspector.get_columns("voice_governance_idempotency")}
    idempotency_indexes = {item.get("name") for item in inspector.get_indexes("voice_governance_idempotency")}
    return feedback_constraints, idempotency_columns, idempotency_indexes


def test_voice_governance_migration_up_down_and_reupgrade(tmp_path: Path) -> None:
    database = tmp_path / "voice-migrations.db"
    _alembic(database, "upgrade", "head")
    constraints, columns, indexes = _voice_schema(database)
    assert "uq_voice_feedback_scope_source_kind" in constraints
    assert "expires_at" in columns
    assert "ix_voice_governance_idempotency_expires_at" in indexes
    live_inspector = sa.inspect(sa.create_engine(f"sqlite:///{database}"))
    assert "voice_live_runs" in live_inspector.get_table_names()
    assert "voice_live_run_segments" in live_inspector.get_table_names()
    assert "reported_gap_sequences" in {item["name"] for item in live_inspector.get_columns("voice_live_runs")}
    assert "timeline_revision" in {
        item["name"] for item in live_inspector.get_columns("voice_live_runs")
    }
    live_segment_columns = {
        item["name"] for item in live_inspector.get_columns("voice_live_run_segments")
    }
    assert {
        "provisional_result_ref",
        "correction_task_id",
        "correction_status",
        "correction_spec_ref",
        "text_revision",
        "timeline_revision",
    } <= live_segment_columns

    _alembic(database, "downgrade", VOICE_PRE_TTL_REVISION)
    constraints, columns, indexes = _voice_schema(database)
    assert "uq_voice_feedback_scope_source_kind" not in constraints
    assert "expires_at" not in columns
    assert "ix_voice_governance_idempotency_expires_at" not in indexes

    _alembic(database, "upgrade", "head")
    constraints, columns, indexes = _voice_schema(database)
    assert "uq_voice_feedback_scope_source_kind" in constraints
    assert "expires_at" in columns
    assert "ix_voice_governance_idempotency_expires_at" in indexes
