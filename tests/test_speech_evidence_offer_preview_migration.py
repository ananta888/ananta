from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine


def test_offer_preview_migration_is_additive_restart_safe_and_reversible(monkeypatch) -> None:
    database = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    migration = importlib.import_module(
        "migrations.versions.a9b0c1d2e3f4_add_signed_speech_evidence_offer_previews"
    )
    with database.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE speech_evidence_offers "
                "(offer_id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO speech_evidence_offers (offer_id, state) "
                "VALUES ('legacy-offer', 'invalidated')"
            )
        )
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))

        migration.upgrade()
        migration.upgrade()

        columns = {row["name"] for row in inspect(connection).get_columns("speech_evidence_offers")}
        assert {"group_previews", "group_preview_digest", "protocol_version"} <= columns
        row = connection.execute(
            sa.text(
                "SELECT group_previews, group_preview_digest, protocol_version "
                "FROM speech_evidence_offers WHERE offer_id = 'legacy-offer'"
            )
        ).one()
        assert row[0] == "[]"
        assert row[1] == migration._EMPTY_PREVIEW_DIGEST
        assert row[2] == "ananta.speech-evidence-sync.v1"

        migration.downgrade()
        columns = {row["name"] for row in inspect(connection).get_columns("speech_evidence_offers")}
        assert columns == {"offer_id", "state"}
        assert connection.execute(
            sa.text("SELECT state FROM speech_evidence_offers WHERE offer_id = 'legacy-offer'")
        ).scalar_one() == "invalidated"
