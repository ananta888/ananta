from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pytest import MonkeyPatch

from agent.db_models.business_controlling import (
    BusinessControllingMappingDB,
    BusinessControllingProfileDB,
)
from agent.db_models.scientific_skill_provenance import (
    ScientificSkillProvenanceReceiptDB,
)


def test_business_controlling_migration_adopts_preexisting_model_tables(
    monkeypatch: MonkeyPatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.f6b8c0d2e4a7_add_business_controlling_profiles"
    )
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    BusinessControllingProfileDB.__table__.to_metadata(metadata)
    BusinessControllingMappingDB.__table__.to_metadata(metadata)
    metadata.create_all(engine)

    with engine.begin() as connection:
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        migration.upgrade()

    inspector = sa.inspect(engine)
    assert {
        "business_controlling_profiles",
        "business_controlling_mappings",
    } <= set(inspector.get_table_names())
    assert "ix_business_controlling_profile_scope" in {
        index["name"]
        for index in inspector.get_indexes("business_controlling_profiles")
    }
    assert "ix_business_controlling_mapping_scope" in {
        index["name"]
        for index in inspector.get_indexes("business_controlling_mappings")
    }


def test_scientific_skill_migration_adopts_preexisting_model_table(
    monkeypatch: MonkeyPatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.a8c0e2f4b6d9_add_scientific_skill_provenance"
    )
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    ScientificSkillProvenanceReceiptDB.__table__.to_metadata(metadata)
    metadata.create_all(engine)

    with engine.begin() as connection:
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        migration.upgrade()

    inspector = sa.inspect(engine)
    assert "scientific_skill_provenance_receipts" in inspector.get_table_names()
    assert "ix_scientific_skill_receipt_scope" in {
        index["name"]
        for index in inspector.get_indexes("scientific_skill_provenance_receipts")
    }
