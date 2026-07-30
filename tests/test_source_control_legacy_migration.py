from __future__ import annotations

import json
import re
from dataclasses import fields, replace
from pathlib import Path

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    ActiveKnowledgeIndexEventDB,
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantAuditDB,
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceRevisionDB,
)
from agent.db_models.source_control_migration import (
    SourceControlLegacyMappingDB,
    SourceControlMigrationRunDB,
    SourceRefMappingDB,
)
from agent.repositories.source_control_migration_repository import (
    SQLSourceControlMigrationRepository,
)
from agent.services.source_control_legacy_migration import (
    LegacyMigrationInventory,
    SourceControlLegacyMigrationService,
    SourceControlMigrationError,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "source_control"
    / "legacy_migration.v1.json"
)
TABLES = [
    SourceConnectionDB.__table__,
    SourceRevisionDB.__table__,
    SourceAccessGrantDB.__table__,
    SourceAccessGrantAuditDB.__table__,
    KnowledgeIndexSourceBindingDB.__table__,
    KnowledgeIndexRunSourceBindingDB.__table__,
    ActiveKnowledgeIndexDB.__table__,
    ActiveKnowledgeIndexEventDB.__table__,
    SourceRefMappingDB.__table__,
    SourceControlMigrationRunDB.__table__,
    SourceControlLegacyMappingDB.__table__,
]


def _inventory() -> LegacyMigrationInventory:
    return LegacyMigrationInventory.from_mapping(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )


def _repository(path, *, hook=None):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine, tables=TABLES)
    return engine, SQLSourceControlMigrationRepository(
        engine,
        clock=lambda: 10_000.0,
        apply_fault_hook=hook,
    )


def test_dry_run_is_deterministic_and_has_no_side_effects(tmp_path) -> None:
    engine, repository = _repository(tmp_path / "migration.sqlite3")
    service = SourceControlLegacyMigrationService(repository=repository)
    inventory = _inventory()

    first = service.migrate(inventory, dry_run=True)
    second = service.migrate(inventory, dry_run=True)

    assert first == second
    assert first.state == "planned"
    assert first.planned_entries == 5
    assert first.counts.total == 5
    assert repository.get_run(first.migration_id) is None
    with Session(engine) as db:
        assert db.exec(select(SourceConnectionDB)).all() == []
        assert db.exec(select(SourceAccessGrantDB)).all() == []


def test_apply_is_idempotent_and_never_creates_authority(tmp_path) -> None:
    engine, repository = _repository(tmp_path / "migration.sqlite3")
    service = SourceControlLegacyMigrationService(repository=repository)
    inventory = _inventory()

    applied = service.migrate(inventory, dry_run=False)
    replay = service.migrate(inventory, dry_run=False)
    mappings = repository.list_mappings(applied.migration_id)

    assert applied.state == replay.state == "applied"
    assert applied.applied_entries == replay.applied_entries == 5
    assert [mapping.legacy_kind for mapping in mappings] == [
        "source_snapshot",
        "context_policy",
        "knowledge_index",
        "index_run",
        "citation",
    ]
    assert mappings[2].policy_snapshot_id == "policy-snapshot-alpha-v7"
    assert mappings[4].source_ref_id is not None
    with Session(engine) as db:
        assert len(db.exec(select(SourceConnectionDB)).all()) == 1
        assert len(db.exec(select(SourceRevisionDB)).all()) == 1
        assert len(db.exec(select(SourceRefMappingDB)).all()) == 1
        assert len(
            db.exec(select(KnowledgeIndexSourceBindingDB)).all()
        ) == 1
        assert len(
            db.exec(select(KnowledgeIndexRunSourceBindingDB)).all()
        ) == 1
        assert db.exec(select(SourceAccessGrantDB)).all() == []
        assert db.exec(select(ActiveKnowledgeIndexDB)).all() == []


def test_abort_rolls_back_current_entry_and_resume_continues(tmp_path) -> None:
    failed = set()

    def fail_once(entry):
        if entry.sequence == 3 and entry.sequence not in failed:
            failed.add(entry.sequence)
            raise RuntimeError("simulated_partial_failure")

    _engine, repository = _repository(
        tmp_path / "migration.sqlite3",
        hook=fail_once,
    )
    service = SourceControlLegacyMigrationService(repository=repository)
    inventory = _inventory()

    aborted = service.migrate(inventory, dry_run=False)
    mappings_after_abort = repository.list_mappings(aborted.migration_id)
    resumed = service.migrate(
        inventory,
        dry_run=False,
        resume=True,
    )

    assert aborted.state == "aborted"
    assert aborted.applied_entries == 2
    assert [mapping.sequence for mapping in mappings_after_abort] == [1, 2]
    assert resumed.state == "applied"
    assert resumed.applied_entries == 5
    assert [mapping.sequence for mapping in repository.list_mappings(
        resumed.migration_id
    )] == [1, 2, 3, 4, 5]


def test_rollback_removes_only_new_mappings_and_keeps_source_facts(
    tmp_path,
) -> None:
    engine, repository = _repository(tmp_path / "migration.sqlite3")
    service = SourceControlLegacyMigrationService(repository=repository)
    applied = service.migrate(_inventory(), dry_run=False)

    rolled_back = service.rollback(applied.migration_id)

    assert rolled_back.state == "rolled_back"
    assert repository.list_mappings(applied.migration_id) == ()
    with Session(engine) as db:
        assert len(db.exec(select(SourceConnectionDB)).all()) == 1
        assert len(db.exec(select(SourceRevisionDB)).all()) == 1
        assert db.exec(select(SourceRefMappingDB)).all() == []
        assert db.exec(select(KnowledgeIndexSourceBindingDB)).all() == []
        assert db.exec(
            select(KnowledgeIndexRunSourceBindingDB)
        ).all() == []
        assert db.exec(select(SourceAccessGrantDB)).all() == []


def test_missing_policy_binding_blocks_apply_without_writes(tmp_path) -> None:
    engine, repository = _repository(tmp_path / "migration.sqlite3")
    service = SourceControlLegacyMigrationService(repository=repository)
    inventory = _inventory()
    broken_index = replace(
        inventory.knowledge_indexes[0],
        legacy_policy_key="missing-policy",
    )
    broken = replace(inventory, knowledge_indexes=(broken_index,))

    dry_run = service.migrate(broken, dry_run=True)
    assert dry_run.state == "blocked"
    assert {
        issue.reason_code for issue in dry_run.issues if issue.blocking
    } == {"legacy_index_binding_unverified", "legacy_run_index_unverified"}
    with pytest.raises(
        SourceControlMigrationError,
        match="source_control_migration_plan_blocked",
    ):
        service.migrate(broken, dry_run=False)
    with Session(engine) as db:
        assert db.exec(select(SourceConnectionDB)).all() == []
        assert db.exec(select(SourceControlMigrationRunDB)).all() == []


def test_fixture_does_not_invent_grounded_source_or_run_ids() -> None:
    fixture_text = FIXTURE.read_text(encoding="utf-8")
    assert re.search(r"\b(?:SRC|RUN)_[A-Za-z0-9_-]+\b", fixture_text) is None


def test_grown_inventory_reuses_prior_run_mappings(tmp_path) -> None:
    engine, repository = _repository(tmp_path / "migration.sqlite3")
    service = SourceControlLegacyMigrationService(repository=repository)
    inventory = _inventory()
    first = service.migrate(inventory, dry_run=False)

    citation_field = next(
        field.name
        for field in fields(inventory)
        if "citation" in field.name
    )
    citations = getattr(inventory, citation_field)
    original_citation = citations[0]
    citation_key_field = next(
        field.name
        for field in fields(original_citation)
        if "key" in field.name and "legacy" in field.name
    )
    grown_citation = replace(
        original_citation,
        **{
            citation_key_field: (
                f"{getattr(original_citation, citation_key_field)}-grown"
            )
        },
    )
    grown_inventory = replace(
        inventory,
        **{citation_field: (*citations, grown_citation)},
    )

    second = service.migrate(grown_inventory, dry_run=False)
    second_mappings = repository.list_mappings(second.migration_id)

    assert first.migration_id != second.migration_id
    assert second.state == "applied"
    assert len(second_mappings) == 6
    with Session(engine) as db:
        assert len(db.exec(select(SourceConnectionDB)).all()) == 1
        assert len(db.exec(select(SourceRevisionDB)).all()) == 1
        assert len(db.exec(select(KnowledgeIndexSourceBindingDB)).all()) == 1
        assert len(
            db.exec(select(KnowledgeIndexRunSourceBindingDB)).all()
        ) == 1
        assert len(db.exec(select(SourceRefMappingDB)).all()) == 1


@pytest.mark.parametrize(
    "inventory_field",
    ["context_policies", "knowledge_indexes", "index_runs"],
)
def test_authority_and_artifact_sha256_must_be_lowercase(
    tmp_path,
    inventory_field,
) -> None:
    engine, repository = _repository(
        tmp_path / f"{inventory_field}.sqlite3"
    )
    service = SourceControlLegacyMigrationService(repository=repository)
    inventory = _inventory()
    entries = getattr(inventory, inventory_field)
    entry = entries[0]
    digest_field = next(
        field.name
        for field in fields(entry)
        if isinstance(getattr(entry, field.name), str)
        and len(getattr(entry, field.name)) == 64
        and all(
            char in "0123456789abcdef"
            for char in getattr(entry, field.name)
        )
    )
    invalid_entry = replace(entry, **{digest_field: "A" * 64})
    invalid_inventory = replace(
        inventory,
        **{inventory_field: (invalid_entry, *entries[1:])},
    )

    plan = service.migrate(invalid_inventory, dry_run=True)

    assert plan.state == "blocked"
    assert any(issue.blocking for issue in plan.issues)
    assert repository.get_run(plan.migration_id) is None
    with Session(engine) as db:
        assert db.exec(select(SourceControlLegacyMappingDB)).all() == []
