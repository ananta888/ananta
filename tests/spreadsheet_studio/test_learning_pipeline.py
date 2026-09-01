from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.spreadsheet_learning_service import SpreadsheetLearningService
from agent.services.spreadsheet_learning_store import SpreadsheetLearningStore
from tests.spreadsheet_studio.helpers import proposal, service, snapshot


def _feedback(document: dict, *, event_id: str = "feedback-one") -> dict:
    return {
        "schema": "ananta.spreadsheet-feedback-command.v1",
        "event_id": event_id,
        "document_id": document["document_id"],
        "proposal_id": "proposal-one",
        "kind": "accepted",
        "instruction": "Update alice@example.test with token=super-secret-value",
        "correction_actions": [],
        "excluded_cells": [],
    }


def _consent(event: dict, *, consent_id: str = "consent-one") -> dict:
    return {
        "schema": "ananta.spreadsheet-training-consent-command.v1",
        "consent_id": consent_id,
        "feedback_id": event["event_id"],
        "record_digest": event["record_digest"],
        "purpose": "spreadsheet_action_training",
        "retention_days": 30,
        "granted": True,
    }


def _dataset(event: dict, *, dataset_id: str = "dataset-one") -> dict:
    return {
        "schema": "ananta.spreadsheet-dataset-command.v1",
        "dataset_id": dataset_id,
        "feedback_ids": [event["event_id"]],
        "recipe_version": "recipe-v1",
        "split_seed": "split-v1",
        "split_percent": {"train": 70, "validation": 10, "eval": 10, "test": 10},
    }


def _learning(tmp_path: Path, studio) -> SpreadsheetLearningService:
    return SpreadsheetLearningService(
        documents=studio._store,
        store=SpreadsheetLearningStore(tmp_path / "learning.sqlite3"),
        dataset_root=tmp_path / "datasets",
        clock=lambda: 1_800_000_000.0,
    )


def test_feedback_consent_dataset_and_revocation_are_separate_automatic_states(tmp_path: Path) -> None:
    studio = service(tmp_path / "documents.sqlite3")
    document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="user-a",
        title="Budget",
        snapshot=snapshot(),
        document_id="document-one",
    )
    studio.execute_proposal(tenant_id="tenant-a", principal_id="user-a", proposal=proposal(document))
    learning = _learning(tmp_path, studio)

    event = learning.record_feedback(
        tenant_id="tenant-a",
        principal_id="user-a",
        payload=_feedback(document),
    )
    assert "alice@example.test" not in json.dumps(event)
    assert "super-secret-value" not in json.dumps(event)
    preview = learning.privacy_preview(tenant_id="tenant-a", principal_id="user-a", event_id=event["event_id"])
    assert preview["record_digest"] == event["record_digest"]

    consent = learning.grant_consent(
        tenant_id="tenant-a",
        principal_id="user-a",
        payload=_consent(event),
    )
    assert consent["state"] == "active"
    dataset = learning.materialize_dataset(
        tenant_id="tenant-a",
        principal_id="user-a",
        payload=_dataset(event),
    )
    assert dataset["record_count"] == 1
    assert dataset["readiness"]["dry_run_ready"] is True
    assert dataset["readiness"]["training_ready"] is False
    path = learning.dataset_path(tenant_id="tenant-a", principal_id="user-a", dataset_id="dataset-one")
    row = json.loads(path.read_text().strip())
    assert row["task_kind"] == "spreadsheet_actions"
    assert row["consent_digest"] == consent["consent_digest"]

    revoked = learning.revoke_consent(
        tenant_id="tenant-a",
        principal_id="user-a",
        consent_id="consent-one",
        expected_version=1,
    )
    assert revoked["state"] == "revoked"
    assert revoked["impact"]["dataset_ids"] == ["dataset-one"]
    quarantined = learning.get_dataset(
        tenant_id="tenant-a",
        principal_id="user-a",
        dataset_id="dataset-one",
    )
    assert quarantined["state"] == "quarantined"
    assert quarantined["readiness"]["dry_run_ready"] is False
    assert quarantined["revocation_impact"]["mathematical_unlearning_claimed"] is False
    with pytest.raises(PermissionError, match="consent_inactive"):
        learning.materialize_dataset(
            tenant_id="tenant-a",
            principal_id="user-a",
            payload=_dataset(event, dataset_id="dataset-after-revoke"),
        )


def test_training_lineage_is_included_in_automatic_revocation_fencing(tmp_path: Path) -> None:
    studio = service(tmp_path / "documents.sqlite3")
    document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="user-a",
        title="Budget",
        snapshot=snapshot(),
        document_id="document-one",
    )
    studio.execute_proposal(tenant_id="tenant-a", principal_id="user-a", proposal=proposal(document))
    learning = _learning(tmp_path, studio)
    event = learning.record_feedback(tenant_id="tenant-a", principal_id="user-a", payload=_feedback(document))
    learning.grant_consent(tenant_id="tenant-a", principal_id="user-a", payload=_consent(event))
    dataset = learning.materialize_dataset(
        tenant_id="tenant-a",
        principal_id="user-a",
        payload=_dataset(event),
    )
    learning.record_training_lineage(
        tenant_id="tenant-a",
        principal_id="user-a",
        dataset_id=dataset["dataset_id"],
        ml_intern_dataset_id="ml-dataset-one",
        job={"id": "job-one"},
    )

    revoked = learning.revoke_consent(
        tenant_id="tenant-a",
        principal_id="user-a",
        consent_id="consent-one",
        expected_version=1,
    )

    assert revoked["impact"]["state"] == "fence_required"
    assert revoked["impact"]["training_jobs"] == [
        {"job_id": "job-one", "owner_id": "user-a", "state": "fence_required"}
    ]


def test_apply_or_feedback_never_implies_training_consent(tmp_path: Path) -> None:
    studio = service(tmp_path / "documents.sqlite3")
    document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="user-a",
        title="Budget",
        snapshot=snapshot(),
        document_id="document-one",
    )
    studio.execute_proposal(tenant_id="tenant-a", principal_id="user-a", proposal=proposal(document))
    learning = _learning(tmp_path, studio)
    event = learning.record_feedback(tenant_id="tenant-a", principal_id="user-a", payload=_feedback(document))
    with pytest.raises(KeyError, match="consent_not_found"):
        learning.materialize_dataset(
            tenant_id="tenant-a",
            principal_id="user-a",
            payload=_dataset(event),
        )
