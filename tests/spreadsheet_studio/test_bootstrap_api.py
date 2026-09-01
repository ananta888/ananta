from __future__ import annotations

import hashlib
import io

from flask import Flask

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
    executed = client.post(
        "/api/spreadsheet-studio/proposals/execute",
        headers=admin_auth_header,
        json=proposal(document, proposal_id="api-proposal"),
    )
    assert executed.status_code == 201
    result = executed.get_json()["data"]
    assert result["state"] == "promoted"
    assert result["human_intervention_required"] is False
    listed = client.get("/api/spreadsheet-studio/documents", headers=admin_auth_header)
    assert listed.status_code == 200
    assert listed.get_json()["data"]["items"][0]["version"] == 2


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
