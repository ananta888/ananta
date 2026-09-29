from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlmodel import create_engine

from agent.db_models.speech_adaptation import (
    SpeechAdaptationArtifactDB,
    SpeechAdaptationCapacityLeaseDB,
    SpeechAdaptationJobDB,
)


def test_speech_adaptation_migration_matches_models_and_is_reversible(monkeypatch) -> None:
    db = create_engine("sqlite://")
    migration = importlib.import_module("migrations.versions.c4d5e6f7a8b9_add_speech_adaptation_control_plane")
    with db.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_voice (id VARCHAR PRIMARY KEY)"))
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        migration.upgrade()
        inspector = inspect(connection)
        models = {
            "speech_adaptation_jobs": SpeechAdaptationJobDB,
            "speech_adaptation_capacity_leases": SpeechAdaptationCapacityLeaseDB,
            "speech_adaptation_artifacts": SpeechAdaptationArtifactDB,
        }
        assert {"legacy_voice", *models} <= set(inspector.get_table_names())
        for table_name, model in models.items():
            reflected = {item["name"] for item in inspector.get_columns(table_name)}
            assert reflected == {column.name for column in model.__table__.columns}
        assert {"uq_speech_adaptation_job_scope_idempotency"} <= {
            item["name"] for item in inspector.get_unique_constraints("speech_adaptation_jobs")
        }
        assert {
            "uq_speech_adaptation_capacity_job",
            "uq_speech_adaptation_capacity_lease",
            "uq_speech_adaptation_capacity_epoch",
        } <= {item["name"] for item in inspector.get_unique_constraints("speech_adaptation_capacity_leases")}
        assert any(
            item["referred_table"] == "speech_adaptation_jobs"
            for item in inspector.get_foreign_keys("speech_adaptation_artifacts")
        )
        assert {
            "uq_speech_adaptation_artifact_attempt_ref",
            "uq_speech_adaptation_artifact_attempt_media",
        } <= {item["name"] for item in inspector.get_unique_constraints("speech_adaptation_artifacts")}
        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {"legacy_voice"}
