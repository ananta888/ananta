from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from jsonschema import Draft202012Validator
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine

import agent.db_models  # noqa: F401 - registers SQLModel metadata
from agent.repositories.spreadsheet_document_repository import SqlSpreadsheetDocumentRepository
from agent.repositories.spreadsheet_learning_repository import SqlSpreadsheetLearningRepository
from agent.services.spreadsheet_dataset_split_service import SpreadsheetDatasetSplitService
from agent.services.spreadsheet_learning_repository_port import SpreadsheetLearningConflict
from ananta_contracts.spreadsheet_studio import canonical_digest, canonical_json
from tests.spreadsheet_studio.helpers import snapshot


def _row(*, feedback_id: str, digest: str, lineage: str, instruction: str) -> dict:
    return {
        "instruction": instruction,
        "input": '{"schema":"ananta.spreadsheet-training-context.v1","cells":[]}',
        "output": '{"schema":"ananta.spreadsheet-action-output.v1","actions":[]}',
        "task_kind": "spreadsheet_actions",
        "privacy_class": "consented_masked",
        "quality_label": "accepted",
        "source_document_version": 1,
        "record_digest": digest,
        "feedback_id": feedback_id,
        "consent_id": f"consent-{feedback_id}",
        "consent_digest": "c" * 64,
        "lineage_root_id": lineage,
        "recipe_version": "recipe-v1",
    }


def test_clustered_split_lock_is_deterministic_and_never_splits_related_records() -> None:
    service = SpreadsheetDatasetSplitService()
    rows = [
        _row(feedback_id="one", digest="1" * 64, lineage="document-a", instruction="Set value 10"),
        _row(feedback_id="two", digest="2" * 64, lineage="document-b", instruction="Set value 11"),
        _row(feedback_id="three", digest="3" * 64, lineage="document-c", instruction="Archive unrelated notes"),
        _row(feedback_id="duplicate", digest="1" * 64, lineage="document-z", instruction="Set value 10"),
    ]
    split = {"train": 50, "validation": 20, "eval": 20, "test": 10}

    first = service.prepare(rows, seed="stable-seed", split_percent=split)
    second = service.prepare(list(reversed(rows)), seed="stable-seed", split_percent=split)

    assert first.rows == second.rows
    assert first.split_lock == second.split_lock
    # Canonical feedback-id ordering selects the same representative regardless
    # of caller order and records the other exact duplicate as excluded.
    assert first.excluded_feedback_ids == ("one",)
    assignments = first.split_lock["record_assignments"]
    assert assignments["1" * 64] == assignments["2" * 64]
    assert len(first.split_lock["cluster_digest"]) == 64
    assert len(first.split_lock["split_lock_digest"]) == 64
    assert first.split_lock["algorithm_version"] == "spreadsheet-connected-leakage-clusters.v1"
    schema = json.loads(
        (Path(__file__).parents[2] / "schemas/spreadsheet-studio/dataset-split-lock.v1.json").read_text()
    )
    Draft202012Validator(schema).validate(first.split_lock)


def test_sql_learning_repository_is_tenant_scoped_and_revocation_intent_is_atomic(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'learning.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    documents = SqlSpreadsheetDocumentRepository(db_engine=engine)
    documents.create_document(
        "tenant-a",
        {
            "schema": "ananta.spreadsheet-document-version.v1",
            "document_id": "document-one",
            "owner_id": "owner-one",
            "snapshot_digest": canonical_digest(snapshot()),
            "snapshot": snapshot(),
            "state": "published",
        },
    )
    repository = SqlSpreadsheetLearningRepository(db_engine=engine)
    event = {
        "event_id": "feedback-one",
        "owner_id": "owner-one",
        "document_id": "document-one",
        "record_digest": "1" * 64,
    }
    event["digest"] = canonical_digest(event)
    repository.append_feedback("tenant-a", event)
    assert repository.append_feedback("tenant-a", event)["replayed"] is True
    with pytest.raises(KeyError, match="feedback_not_found"):
        repository.get_feedback("tenant-b", "feedback-one")

    consent_v1 = {
        "consent_id": "consent-one",
        "feedback_id": "feedback-one",
        "owner_id": "owner-one",
        "state": "active",
        "version": 1,
    }
    consent_v1["consent_digest"] = canonical_digest(consent_v1)
    repository.append_consent("tenant-a", consent_v1)
    existing_impact = {
        "impact_id": "impact-one",
        "consent_id": "consent-one",
    }
    existing_impact["digest"] = canonical_digest(existing_impact)
    repository.append_revocation_impact("tenant-a", existing_impact)
    consent_v2 = {key: value for key, value in consent_v1.items() if key != "consent_digest"}
    consent_v2.update(state="revoked", version=2)
    consent_v2["consent_digest"] = canonical_digest(consent_v2)
    conflicting_impact = {"impact_id": "impact-one", "consent_id": "consent-one", "state": "changed"}
    conflicting_impact["digest"] = canonical_digest(conflicting_impact)

    with pytest.raises(SpreadsheetLearningConflict, match="revocation_impact_replay_conflict"):
        repository.append_consent_with_impact("tenant-a", consent_v2, conflicting_impact)

    assert repository.get_consent("tenant-a", "consent-one")["version"] == 1
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE spreadsheet_feedback_events SET payload_json=:payload "
                "WHERE tenant_id='tenant-a' AND event_id='feedback-one'"
            ),
            {"payload": canonical_json({**event, "owner_id": "tampered-owner"})},
        )
    with pytest.raises(RuntimeError, match="payload_integrity_failed"):
        repository.append_feedback("tenant-a", event)


def test_learning_store_migration_is_reversible(tmp_path, monkeypatch) -> None:
    documents = importlib.import_module("migrations.versions.b9d1f3a5c7e0_add_spreadsheet_document_persistence")
    learning = importlib.import_module("migrations.versions.f3b5d7e9a1c4_add_spreadsheet_learning_store")
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite3'}")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(documents, "op", operations)
        monkeypatch.setattr(learning, "op", operations)
        documents.upgrade()
        learning.upgrade()
        assert {
            "spreadsheet_feedback_events",
            "spreadsheet_training_consents",
            "spreadsheet_datasets",
            "spreadsheet_training_lineage",
            "spreadsheet_consent_revocation_impacts",
        }.issubset(inspect(connection).get_table_names())
        learning.downgrade()
        assert "spreadsheet_datasets" not in inspect(connection).get_table_names()
        documents.downgrade()
