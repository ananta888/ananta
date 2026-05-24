from __future__ import annotations

import pytest

from agent.services.browser_artifact_service import BrowserArtifactService


def test_schema_validation_rejects_missing_fields():
    svc = BrowserArtifactService()
    v = svc.validate_schema({"extracted_data": {}})
    assert v.valid is False


def test_persist_with_provenance_works(tmp_path, monkeypatch):
    from agent.services import artifact_store as mod
    mod.artifact_store.base_dir = tmp_path

    svc = BrowserArtifactService()
    payload = {
        "extracted_data": {"title": "x"},
        "page_evidence": [{"url": "https://example.com"}],
        "sources": [{"url": "https://example.com"}],
    }
    out = svc.persist_with_provenance(artifact_id="a1", version_number=1, payload=payload)
    assert out["provenance"]["schema"] == "browser-artifact.v1"
