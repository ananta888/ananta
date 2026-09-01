from __future__ import annotations

import hashlib
import io

from flask import Flask
from sqlmodel import SQLModel, create_engine

import agent.db_models  # noqa: F401 - registers SQLModel metadata
from agent.adapters.spreadsheet_mock_execution_adapter import DeterministicSpreadsheetMockExecutionAdapter
from agent.bootstrap.spreadsheet_studio import initialize_spreadsheet_studio
from agent.services.spreadsheet_artifact_store import SpreadsheetArtifactStore
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService
from agent.services.spreadsheet_store import SpreadsheetStore
from ananta_contracts.spreadsheet_studio import WorkbookSnapshotV1
from tests.spreadsheet_studio.helpers import proposal, snapshot


def _wire(app: Flask, tmp_path, *, enabled: bool = True, automatic: bool = True) -> None:
    app.config.update(
        ROLE="hub",
        ANANTA_SPREADSHEET_STUDIO_ENABLED=enabled,
        ANANTA_SPREADSHEET_STUDIO_MODE="mock" if enabled else "disabled",
        ANANTA_SPREADSHEET_STUDIO_AUTOMATIC_PROMOTION_ENABLED=automatic,
        ANANTA_SPREADSHEET_STUDIO_STATE=str(tmp_path / "spreadsheet.sqlite3"),
    )
    initialize_spreadsheet_studio(app)


def test_composition_is_default_off_and_hub_only(tmp_path) -> None:
    hub = Flask("hub")
    hub.config["ROLE"] = "hub"
    status = initialize_spreadsheet_studio(hub)
    assert status.ready is False
    assert status.reason_code == "spreadsheet_studio_disabled"

    worker = Flask("worker")
    worker.config.update(ROLE="worker", ANANTA_SPREADSHEET_STUDIO_ENABLED=True, ANANTA_SPREADSHEET_STUDIO_MODE="mock")
    status = initialize_spreadsheet_studio(worker)
    assert status.ready is False
    assert status.reason_code == "spreadsheet_hub_role_required"


def test_worker_mode_uses_hub_queue_instead_of_synchronous_http(monkeypatch, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'worker-mode.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("agent.database.engine", engine)
    hub = Flask("queue-hub")
    hub.config.update(
        ROLE="hub",
        ANANTA_SPREADSHEET_STUDIO_ENABLED=True,
        ANANTA_SPREADSHEET_STUDIO_MODE="worker",
        ANANTA_SPREADSHEET_STUDIO_STATE=str(tmp_path / "queue-state.sqlite3"),
        ANANTA_SPREADSHEET_WORKER_ID="spreadsheet-worker",
    )

    status = initialize_spreadsheet_studio(hub)

    assert status.ready is True
    assert "spreadsheet_proposal_execution_service" in hub.extensions
    assert hub.extensions["spreadsheet_learning_service"]._store.production_component is True
    assert hub.extensions["spreadsheet_training_admission_service"]._repository.production_component is True
    capability = hub.extensions["spreadsheet_studio_service"].capabilities()
    assert capability["executor"]["execution_mode"] == "queue_and_lease"


def test_internal_claim_route_requires_worker_token(app, client) -> None:
    class Ingress:
        def claim(self, *, worker_id):
            return {"schema": "assignment", "worker_id": worker_id}

    app.config.update(
        ANANTA_SPREADSHEET_WORKER_TOKEN="spreadsheet-static-token-000000",
        ANANTA_SPREADSHEET_WORKER_ID="spreadsheet-worker",
    )
    app.extensions["spreadsheet_execution_ingress_service"] = Ingress()
    denied = client.post(
        "/api/spreadsheet-studio/internal/jobs/claim",
        json={"worker_id": "spreadsheet-worker"},
    )
    assert denied.status_code == 401
    accepted = client.post(
        "/api/spreadsheet-studio/internal/jobs/claim",
        headers={"Authorization": "Bearer spreadsheet-static-token-000000"},
        json={"worker_id": "spreadsheet-worker"},
    )
    assert accepted.status_code == 200
    assert accepted.get_json()["data"]["worker_id"] == "spreadsheet-worker"
    wrong_worker = client.post(
        "/api/spreadsheet-studio/internal/jobs/claim",
        headers={"Authorization": "Bearer spreadsheet-static-token-000000"},
        json={"worker_id": "another-worker"},
    )
    assert wrong_worker.status_code == 403


def test_adapter_admission_route_is_hub_owned_and_idempotency_bound(app, client, admin_auth_header) -> None:
    captured = {}

    class Admission:
        def admit(self, **kwargs):
            captured.update(kwargs)
            return {
                "schema": "ananta.spreadsheet-adapter-admission.v1",
                "adapter_id": "adapter-one",
                "status": "approved",
                "human_intervention_required": False,
            }

    app.extensions["spreadsheet_adapter_admission_service"] = Admission()
    response = client.post(
        "/api/spreadsheet-studio/adapters/adapter-one/admissions",
        headers={**admin_auth_header, "Idempotency-Key": "adapter-admission-one"},
        json={"adapter_id": "adapter-one", "schema": "ananta.spreadsheet-adapter-admission-command.v1"},
    )

    assert response.status_code == 201
    assert response.get_json()["data"]["status"] == "approved"
    assert captured["payload"]["adapter_id"] == "adapter-one"
    assert captured["idempotency_key"] == "adapter-admission-one"


def test_disabled_capability_is_observable_without_service(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path, enabled=False, automatic=False)
    response = client.get("/api/spreadsheet-studio/capabilities", headers=admin_auth_header)
    assert response.status_code == 200
    assert response.get_json()["data"]["state"] == "disabled"
    assert response.get_json()["data"]["human_intervention_required"] is False


def test_api_runs_document_to_automatic_promotion_without_human(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    created = client.post(
        "/api/spreadsheet-studio/documents",
        headers=admin_auth_header,
        json={"document_id": "api-document", "title": "Budget", "snapshot": snapshot()},
    )
    assert created.status_code == 201
    document = created.get_json()["data"]
    viewport = client.get(
        "/api/spreadsheet-studio/documents/api-document/viewport?sheet_id=sheet-one&start=A1&end=A2&limit=1",
        headers=admin_auth_header,
    )
    assert viewport.status_code == 200
    assert viewport.get_json()["data"]["snapshot_digest"] == document["snapshot_digest"]
    assert viewport.get_json()["data"]["backend_cell_count"] == 2
    reference = client.post(
        "/api/spreadsheet-studio/validation-references",
        headers=admin_auth_header,
        json={"reference_id": "api-reference", "document_id": "api-document", "version": 1},
    )
    assert reference.status_code == 201
    assert reference.get_json()["data"]["snapshot_digest"] == document["snapshot_digest"]
    listed_references = client.get(
        "/api/spreadsheet-studio/validation-references",
        headers=admin_auth_header,
    )
    assert listed_references.status_code == 200
    assert [item["reference_id"] for item in listed_references.get_json()["data"]["items"]] == ["api-reference"]
    fetched_reference = client.get(
        "/api/spreadsheet-studio/validation-references/api-reference",
        headers=admin_auth_header,
    )
    assert fetched_reference.status_code == 200
    assert fetched_reference.get_json()["data"]["human_intervention_required"] is False
    executed = client.post(
        "/api/spreadsheet-studio/proposals/execute",
        headers=admin_auth_header,
        json=proposal(document, proposal_id="api-proposal"),
    )
    assert executed.status_code == 201
    result = executed.get_json()["data"]
    assert result["state"] == "promoted"
    assert result["human_intervention_required"] is False
    diff = client.get(
        "/api/spreadsheet-studio/proposals/api-proposal/diff?offset=0&limit=1",
        headers=admin_auth_header,
    )
    assert diff.status_code == 200
    assert diff.get_json()["data"]["total"] >= 1
    assert len(diff.get_json()["data"]["items"]) == 1
    listed = client.get("/api/spreadsheet-studio/documents", headers=admin_auth_header)
    assert listed.status_code == 200
    assert listed.get_json()["data"]["items"][0]["version"] == 2
    versions = client.get(
        "/api/spreadsheet-studio/documents/api-document/versions",
        headers=admin_auth_header,
    )
    assert versions.status_code == 200
    assert [row["version"] for row in versions.get_json()["data"]["items"]] == [2, 1]
    original = client.get(
        "/api/spreadsheet-studio/documents/api-document/versions/1",
        headers=admin_auth_header,
    )
    assert original.status_code == 200
    assert original.get_json()["data"]["snapshot"]["sheets"][0]["cells"][0]["value"] == 1


def test_api_malformed_and_cross_principal_access_fail_closed(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    malformed = client.post(
        "/api/spreadsheet-studio/documents",
        headers={**admin_auth_header, "Content-Type": "application/json"},
        data="not-json",
    )
    assert malformed.status_code == 422
    missing = client.get(
        "/api/spreadsheet-studio/documents/missing",
        headers=admin_auth_header,
    )
    assert missing.status_code == 404


def test_api_import_persists_opaque_original_and_downloads_it(app, client, admin_auth_header, tmp_path) -> None:
    class ImportAdapter(DeterministicSpreadsheetMockExecutionAdapter):
        def import_document(self, *, content, filename, media_type, document_version_id):
            value = snapshot()
            value["document_version_id"] = document_version_id
            parsed = WorkbookSnapshotV1.from_mapping(value)
            return {
                "schema": "ananta.spreadsheet-import-result.v1",
                "snapshot": parsed.to_dict(),
                "snapshot_digest": parsed.digest,
                "source": {
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                    "format": "xlsx",
                    "media_type": media_type,
                },
                "unsupported_objects": [],
                "engine": "test-import",
                "engine_version": "v1",
                "production_fidelity": True,
            }

    app.extensions["spreadsheet_studio_service"] = SpreadsheetSagaService(
        SpreadsheetStore(tmp_path / "import.sqlite3"),
        policy=SpreadsheetPolicy(enabled=True, mode="mock", automatic_promotion_enabled=True),
        executor=ImportAdapter(),
        artifact_store=SpreadsheetArtifactStore(tmp_path / "artifacts"),
    )
    content = b"deterministic-xlsx-fixture"
    imported = client.post(
        "/api/spreadsheet-studio/documents/import",
        headers=admin_auth_header,
        data={
            "title": "Imported budget",
            "document_id": "imported-budget",
            "file": (
                io.BytesIO(content),
                "budget.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        },
    )
    assert imported.status_code == 201
    document = imported.get_json()["data"]
    assert document["source_artifact"]["artifact_id"].startswith("artifact-")
    assert "path" not in document["source_artifact"]
    downloaded = client.get(
        "/api/spreadsheet-studio/documents/imported-budget/original",
        headers=admin_auth_header,
    )
    assert downloaded.status_code == 200
    assert downloaded.data == content


def test_api_feedback_consent_and_dataset_flow_is_fully_automatic(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    created = client.post(
        "/api/spreadsheet-studio/documents",
        headers=admin_auth_header,
        json={"document_id": "learning-document", "title": "Budget", "snapshot": snapshot()},
    ).get_json()["data"]
    proposal_payload = proposal(created, proposal_id="learning-proposal")
    assert (
        client.post(
            "/api/spreadsheet-studio/proposals/execute",
            headers=admin_auth_header,
            json=proposal_payload,
        ).status_code
        == 201
    )
    feedback = client.post(
        "/api/spreadsheet-studio/feedback",
        headers=admin_auth_header,
        json={
            "schema": "ananta.spreadsheet-feedback-command.v1",
            "event_id": "learning-feedback",
            "document_id": "learning-document",
            "proposal_id": "learning-proposal",
            "kind": "accepted",
            "instruction": "Set the governed value",
            "correction_actions": [],
            "excluded_cells": [],
        },
    )
    assert feedback.status_code == 201
    event = feedback.get_json()["data"]
    preview = client.get(
        "/api/spreadsheet-studio/feedback/learning-feedback/privacy-preview",
        headers=admin_auth_header,
    ).get_json()["data"]
    consent = client.post(
        "/api/spreadsheet-studio/consents",
        headers=admin_auth_header,
        json={
            "schema": "ananta.spreadsheet-training-consent-command.v1",
            "consent_id": "learning-consent",
            "feedback_id": "learning-feedback",
            "record_digest": preview["record_digest"],
            "purpose": "spreadsheet_action_training",
            "retention_days": 30,
            "granted": True,
        },
    )
    assert consent.status_code == 201
    dataset = client.post(
        "/api/spreadsheet-studio/datasets/materialize",
        headers=admin_auth_header,
        json={
            "schema": "ananta.spreadsheet-dataset-command.v1",
            "dataset_id": "learning-dataset",
            "feedback_ids": [event["event_id"]],
            "recipe_version": "recipe-v1",
            "split_seed": "split-v1",
            "split_percent": {"train": 70, "validation": 10, "eval": 10, "test": 10},
        },
    )
    assert dataset.status_code == 201
    result = dataset.get_json()["data"]
    assert result["record_count"] == 1
    assert result["human_intervention_required"] is False
