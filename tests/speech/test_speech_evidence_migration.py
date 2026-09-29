from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlmodel import create_engine


def test_speech_evidence_migration_upgrade_and_downgrade_preserve_legacy(monkeypatch) -> None:
    db = create_engine("sqlite://")
    with db.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_voice (id VARCHAR PRIMARY KEY)"))
        migration = importlib.import_module("migrations.versions.f0a1b2c3d4e5_add_speech_evidence_governance")
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert {
            "legacy_voice",
            "speech_evidence_consents",
            "speech_evidence_keys",
            "speech_evidence",
            "speech_evidence_admissions",
            "speech_curation_tasks",
            "speech_dataset_manifests",
            "speech_lineage_nodes",
            "speech_lineage_edges",
            "speech_lineage_outbox",
            "speech_evidence_revocations",
            "speech_evidence_cleanups",
        } <= set(inspect(connection).get_table_names())
        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {"legacy_voice"}
