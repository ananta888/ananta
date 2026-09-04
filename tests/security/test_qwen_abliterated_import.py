from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.model_artifact_import_service import (
    ModelArtifactAdmissionError,
    ModelArtifactImportRequest,
    ModelArtifactImportService,
    ModelImportPolicyLoader,
    ModelSourceManifestLoader,
)

ROOT = Path(__file__).resolve().parents[2]


def test_sources_are_pinned_distinct_and_evaluation_only() -> None:
    manifest = ModelSourceManifestLoader().load(ROOT / "config/models/qwen3.8-27b-abliterated-sources.v1.json")
    assert len(manifest.artifacts) == 6
    assert all(item.activation == "evaluation_only" for item in manifest.artifacts)
    assert len({item.sha256 for item in manifest.artifacts}) == 6
    assert all(item.license.spdx_id == "Apache-2.0" for item in manifest.artifacts)


def test_hub_admits_exact_research_digest_but_never_production() -> None:
    manifest = ModelSourceManifestLoader().load(ROOT / "config/models/qwen3.8-27b-abliterated-sources.v1.json")
    policy = ModelImportPolicyLoader().load(ROOT / "config/security/model-import-policy.v1.json")
    service = ModelArtifactImportService(manifest, policy)
    values = dict(
        tenant_id="tenant-a",
        project_id="ananta",
        task_id="qabl-import",
        assignment_id="assignment-a",
        dispatch_lease_id="lease-a",
        artifact_id="qwen3.8-27b-abliterated-ud-iq3-xxs",
        expected_sha256="e86259c841888b06fd84750963a0263947c9aae3c3029b52cbb3faa34a554827",
        network_authorized=True,
    )
    assert service.prepare(ModelArtifactImportRequest(**values, purpose="evaluation")).model_format == "gguf"
    with pytest.raises(ModelArtifactAdmissionError, match="model_artifact_production_not_eligible"):
        service.prepare(ModelArtifactImportRequest(**values, purpose="production"))
