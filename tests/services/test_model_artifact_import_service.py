from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.model_artifact_import_service import (
    ModelArtifactAdmissionError,
    ModelArtifactImportRequest,
    ModelArtifactImportService,
    ModelSourceManifestLoader,
)
from ananta_contracts.model_source_manifest import ModelSourceManifest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/models/ornith-1.5-sources.v1.json"


def request(**overrides) -> ModelArtifactImportRequest:
    values = {
        "tenant_id": "tenant-a",
        "project_id": "ananta",
        "task_id": "task-import",
        "assignment_id": "assignment-a",
        "dispatch_lease_id": "lease-a",
        "artifact_id": "ornith-1.5-9b-q4-k-m",
        "expected_sha256": "70c112196e0b7023803c9762752e46d29e612a92c83f995bc3ba1ceb07e8fab6",
        "network_authorized": True,
        "purpose": "evaluation",
    }
    values.update(overrides)
    return ModelArtifactImportRequest(**values)


def test_manifest_is_closed_revision_bound_and_has_no_eligible_release_artifacts() -> None:
    manifest = ModelSourceManifestLoader().load(MANIFEST)

    assert len(manifest.artifacts) == 10
    assert manifest.upstream_claims_are_release_evidence is False
    assert all(len(item.revision) == 40 for item in manifest.artifacts)
    assert all(len(item.sha256) == 64 for item in manifest.artifacts)
    assert all(item.license.status == "declared" for item in manifest.artifacts)
    assert not any(item.activation == "eligible" for item in manifest.artifacts)


def test_hub_prepares_exact_evaluation_assignment_without_downloading() -> None:
    service = ModelArtifactImportService(ModelSourceManifestLoader().load(MANIFEST))

    assignment = service.prepare(request())

    assert assignment.model_id == "ornith-ai/Ornith-1.5-9B-GGUF"
    assert assignment.revision == "abdd624b12ebf020b767fff532ff44fe552b28c3"
    assert assignment.relative_path == "Ornith-1.5-9B-Q4_K_M.gguf"
    assert assignment.expected_size_bytes == 5780090816
    assert assignment.to_dict()["dispatch_lease_id"] == "lease-a"
    assert not any(key.startswith("SRC_") or key.startswith("RUN_") for key in assignment.to_dict())


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"expected_sha256": "a" * 64}, "model_artifact_digest_mismatch"),
        ({"network_authorized": False}, "model_import_network_not_authorized"),
        ({"purpose": "production"}, "model_artifact_production_not_eligible"),
        ({"artifact_id": "missing"}, "model_artifact_unknown"),
        ({"dispatch_lease_id": "../../escape"}, "model_import_binding_invalid"),
    ],
)
def test_admission_fails_closed(overrides, reason) -> None:
    service = ModelArtifactImportService(ModelSourceManifestLoader().load(MANIFEST))

    with pytest.raises(ModelArtifactAdmissionError) as captured:
        service.prepare(request(**overrides))

    assert captured.value.reason_code == reason


def test_manifest_rejects_floating_revision_and_license_promotion_without_text() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["artifacts"][0]["revision"] = "main"
    with pytest.raises(ValueError, match="model_source_revision_invalid"):
        ModelSourceManifest.model_validate(payload)

    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["artifacts"][0]["activation"] = "eligible"
    payload["artifacts"][0]["license"]["status"] = "approved"
    with pytest.raises(ValueError, match="model_license_approval_without_text"):
        ModelSourceManifest.model_validate(payload)


def test_manifest_rejects_traversal_and_duplicate_artifacts() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["artifacts"][0]["relative_path"] = "../model.gguf"
    with pytest.raises(ValueError, match="model_source_path_invalid"):
        ModelSourceManifest.model_validate(payload)

    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["artifacts"].append(payload["artifacts"][0])
    with pytest.raises(ValueError, match="model_source_artifacts_duplicate"):
        ModelSourceManifest.model_validate(payload)
