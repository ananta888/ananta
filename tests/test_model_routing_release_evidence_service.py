from __future__ import annotations

import json

from agent.services.model_routing_release_evidence_service import (
    ModelRoutingReleaseEvidenceService,
    RELEASE_EVIDENCE_SCHEMA,
    REQUIRED_RELEASE_GATES,
    release_source_digests,
)


def _write_evidence(path, repo_root, *, passed=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": RELEASE_EVIDENCE_SCHEMA,
        "suite_revision": "central-model-settings-test",
        "source_digests": release_source_digests(repo_root),
        "gates": {
            gate_id: {"passed": passed, "command": f"test {gate_id}"}
            for gate_id in REQUIRED_RELEASE_GATES
        },
    }), encoding="utf-8")


def test_release_evidence_passes_only_with_complete_fresh_source_digests(tmp_path):
    source = tmp_path / "ananta_contracts/model_selection.py"
    source.parent.mkdir(parents=True)
    source.write_text("release = 1\n", encoding="utf-8")
    evidence = tmp_path / "evidence.json"
    _write_evidence(evidence, tmp_path)
    service = ModelRoutingReleaseEvidenceService(
        repo_root=tmp_path, evidence_path=evidence,
    )

    assert all(check.passed for check in service.checks())

    source.write_text("release = 2\n", encoding="utf-8")
    drifted = service.checks()
    assert not any(check.passed for check in drifted)
    assert {check.reason_code for check in drifted} == {
        "model_routing_release_source_drift"
    }


def test_missing_or_failed_release_evidence_fails_closed(tmp_path):
    missing = ModelRoutingReleaseEvidenceService(
        repo_root=tmp_path, evidence_path=tmp_path / "missing.json",
    ).checks()
    assert len(missing) == len(REQUIRED_RELEASE_GATES)
    assert not any(check.passed for check in missing)

    source = tmp_path / "scripts/model_routing_release_gate.py"
    source.parent.mkdir(parents=True)
    source.write_text("pass\n", encoding="utf-8")
    evidence = tmp_path / "failed.json"
    _write_evidence(evidence, tmp_path, passed=False)
    failed = ModelRoutingReleaseEvidenceService(
        repo_root=tmp_path, evidence_path=evidence,
    ).checks()
    assert {check.reason_code for check in failed} == {
        "model_routing_release_gate_failed"
    }
