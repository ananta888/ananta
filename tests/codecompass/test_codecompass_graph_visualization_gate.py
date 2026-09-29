from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.run_codecompass_graph_visualization_gate import (
    BROWSER_MEASUREMENTS_SCHEMA,
    EVIDENCE_SCHEMA,
    RELEASE_CHECK_EVIDENCE_SCHEMA,
    RELEASE_CHECKS_SCHEMA,
    REPORT_SCHEMA_PATH,
    GateInputError,
    _atomic_write_json,
    _validate_budget_contract,
    _validate_report_schema,
    assemble_evidence,
    build_report,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _create_gate_inputs(tmp_path: Path) -> dict[str, Path | dict]:
    root = tmp_path / "repository"
    source = root / "frontend-angular/a.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const graphGate = true;\n", encoding="utf-8")

    budget_path = root / "config/codecompass/graph_visualization_budgets.v1.json"
    budgets = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema": "ananta.codecompass-graph-visualization-budgets.v1",
        "fixture": {"nodes": 5, "edges": 15, "domains": 3, "hover_events": 10},
        "cache": {"max_revision_profile_entries": 8},
        "operation_limits": {
            "http_requests_per_visual_interaction": 0,
            "score_recomputations_per_hover_burst": 0,
            "renderer_reinitializations_per_profile_change": 0,
            "graph_data_resets_per_profile_change": 0,
            "projection_runs_per_animation_frame": 1,
        },
        "browser_p95_ms": {
            "initial_projection": 750,
            "cached_projection": 40,
            "hover_update": 16,
            "profile_update": 120,
        },
        "evidence_source_paths": [
            "config/codecompass/graph_visualization_budgets.v1.json",
            "frontend-angular/a.ts",
        ],
    }
    _write_json(budget_path, budgets)

    measurements = {
        "schema": BROWSER_MEASUREMENTS_SCHEMA,
        "environment_class": "chromium-headless-test",
        "budget_sha256": _sha256(budget_path),
        "fixture": {"nodes": 5, "edges": 15, "domains": 3, "hover_events": 10},
        "operation_counts": {
            "http_requests_per_visual_interaction": 0,
            "score_recomputations_per_hover_burst": 0,
            "renderer_reinitializations_per_profile_change": 0,
            "graph_data_resets_per_profile_change": 0,
            "projection_runs_per_animation_frame": 1,
        },
        "browser_p95_ms": {
            "initial_projection": 500.0,
            "cached_projection": 20.0,
            "hover_update": 8.0,
            "profile_update": 60.0,
        },
        "cache": {"entries_after_eviction": 8, "deterministic_lru_passed": True},
        "hashes": {
            "graph": "a" * 64,
            "graph_repeat": "a" * 64,
            "profile": "b" * 64,
            "projection": "c" * 64,
            "projection_repeat": "c" * 64,
        },
        "source_hashes": {
            "config/codecompass/graph_visualization_budgets.v1.json": _sha256(budget_path),
            "frontend-angular/a.ts": _sha256(source),
        },
    }
    measurements_path = tmp_path / "browser-measurements.json"
    _write_json(measurements_path, measurements)

    source_hashes = deepcopy(measurements["source_hashes"])
    check_artifacts: dict[str, Path] = {}
    attestations: dict[str, dict] = {}
    release_entries: dict[str, dict] = {}
    for check_id in ("functional", "security", "accessibility", "production_build"):
        check_artifact = root / f"artifacts/test-gates/{check_id}-check.json"
        attestation = {
            "schema": RELEASE_CHECK_EVIDENCE_SCHEMA,
            "check_id": check_id,
            "status": "passed",
            "source_hashes": deepcopy(source_hashes),
        }
        _write_json(check_artifact, attestation)
        check_artifacts[check_id] = check_artifact
        attestations[check_id] = attestation
        release_entries[check_id] = {
            "status": "passed",
            "evidence_path": f"artifacts/test-gates/{check_id}-check.json",
            "evidence_sha256": _sha256(check_artifact),
        }
    release_checks = {
        "schema": RELEASE_CHECKS_SCHEMA,
        "checks": release_entries,
    }
    release_checks_path = tmp_path / "release-checks.json"
    _write_json(release_checks_path, release_checks)
    return {
        "root": root,
        "source": source,
        "budget_path": budget_path,
        "budgets": budgets,
        "measurements_path": measurements_path,
        "measurements": measurements,
        "check_artifacts": check_artifacts,
        "attestations": attestations,
        "release_checks_path": release_checks_path,
        "release_checks": release_checks,
    }


def _assemble(inputs: dict[str, Path | dict]) -> dict:
    return assemble_evidence(
        budget_path=inputs["budget_path"],
        measurements_path=inputs["measurements_path"],
        release_checks_path=inputs["release_checks_path"],
        repository_root=inputs["root"],
    )


def _report(inputs: dict[str, Path | dict], evidence: dict) -> dict:
    return build_report(
        budgets=inputs["budgets"],
        evidence=evidence,
        evidence_path=inputs["root"] / "artifacts/test-gates/final-evidence.json",
        budget_path=inputs["budget_path"],
        repository_root=inputs["root"],
    )


def test_browser_handoff_is_sanitized_hash_bound_and_report_is_schema_valid(tmp_path: Path) -> None:
    inputs = _create_gate_inputs(tmp_path)
    evidence = _assemble(inputs)
    first = _report(inputs, evidence)
    second = _report(inputs, evidence)

    assert evidence["schema"] == EVIDENCE_SCHEMA
    assert evidence["browser_measurements_sha256"] == _sha256(inputs["measurements_path"])
    assert first == second
    assert first["status"] == "passed"
    assert first["source_hashes"] == evidence["source_hashes"]
    report_schema = json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(report_schema)
    assert list(Draft202012Validator(report_schema).iter_errors(first)) == []

    encoded_evidence = json.dumps(evidence, sort_keys=True, allow_nan=False)
    encoded_report = json.dumps(first, sort_keys=True, allow_nan=False)
    for forbidden in (str(tmp_path), "timestamp", "command", "export const graphGate", "secret"):
        assert forbidden not in encoded_evidence
        assert forbidden not in encoded_report


def test_report_fails_closed_when_measurement_exceeds_budget(tmp_path: Path) -> None:
    inputs = _create_gate_inputs(tmp_path)
    measurements = inputs["measurements"]
    measurements["operation_counts"]["http_requests_per_visual_interaction"] = 1
    measurements["browser_p95_ms"]["hover_update"] = 100.0
    _write_json(inputs["measurements_path"], measurements)

    report = _report(inputs, _assemble(inputs))

    assert report["status"] == "failed"
    assert {gate["gate_id"] for gate in report["gates"] if gate["status"] == "failed"} == {
        "operation_counts",
        "browser_p95",
    }


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_budget_contract_rejects_nonpositive_or_nonfinite_timing(value: float) -> None:
    budgets = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema": "ananta.codecompass-graph-visualization-budgets.v1",
        "fixture": {"nodes": 1, "edges": 1, "domains": 1, "hover_events": 1},
        "cache": {"max_revision_profile_entries": 1},
        "operation_limits": {
            "http_requests_per_visual_interaction": 0,
            "score_recomputations_per_hover_burst": 0,
            "renderer_reinitializations_per_profile_change": 0,
            "graph_data_resets_per_profile_change": 0,
            "projection_runs_per_animation_frame": 1,
        },
        "browser_p95_ms": {
            "initial_projection": 1,
            "cached_projection": 1,
            "hover_update": value,
            "profile_update": 1,
        },
        "evidence_source_paths": ["frontend-angular/a.ts"],
    }

    with pytest.raises(GateInputError, match="budgets.browser_p95_ms.hover_update"):
        _validate_budget_contract(budgets)


@pytest.mark.parametrize("mutation", ["missing", "null", "old"])
def test_assembler_rejects_missing_null_or_old_budget_evidence(tmp_path: Path, mutation: str) -> None:
    inputs = _create_gate_inputs(tmp_path)
    measurements = inputs["measurements"]
    if mutation == "missing":
        del measurements["budget_sha256"]
    elif mutation == "null":
        measurements["budget_sha256"] = None
    else:
        measurements["budget_sha256"] = "0" * 64
    _write_json(inputs["measurements_path"], measurements)

    with pytest.raises(GateInputError):
        _assemble(inputs)


def test_assembler_rejects_budget_changed_after_browser_run(tmp_path: Path) -> None:
    inputs = _create_gate_inputs(tmp_path)
    budget_path = inputs["budget_path"]
    budget_path.write_text(budget_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(GateInputError, match="budget_evidence_stale"):
        _assemble(inputs)


@pytest.mark.parametrize("mutation", ["hash", "extra", "source_changed"])
def test_assembler_rejects_tampered_or_stale_source_projection(tmp_path: Path, mutation: str) -> None:
    inputs = _create_gate_inputs(tmp_path)
    measurements = inputs["measurements"]
    if mutation == "hash":
        measurements["source_hashes"]["frontend-angular/a.ts"] = "0" * 64
        _write_json(inputs["measurements_path"], measurements)
    elif mutation == "extra":
        measurements["source_hashes"]["frontend-angular/extra.ts"] = "0" * 64
        _write_json(inputs["measurements_path"], measurements)
    else:
        inputs["source"].write_text("export const graphGate = false;\n", encoding="utf-8")

    with pytest.raises(GateInputError, match="source_(hash_projection_mismatch|evidence_stale)"):
        _assemble(inputs)


@pytest.mark.parametrize("mutation", ["hash", "artifact_changed", "missing_check"])
def test_assembler_rejects_tampered_release_check_evidence(tmp_path: Path, mutation: str) -> None:
    inputs = _create_gate_inputs(tmp_path)
    release_checks = inputs["release_checks"]
    if mutation == "hash":
        release_checks["checks"]["security"]["evidence_sha256"] = "0" * 64
        _write_json(inputs["release_checks_path"], release_checks)
    elif mutation == "artifact_changed":
        inputs["check_artifacts"]["security"].write_text("tampered\n", encoding="utf-8")
    else:
        del release_checks["checks"]["accessibility"]
        _write_json(inputs["release_checks_path"], release_checks)

    with pytest.raises(GateInputError):
        _assemble(inputs)


@pytest.mark.parametrize("mutation", ["schema", "check_id", "status", "source_hash"])
def test_release_entry_cannot_claim_pass_for_unbound_attestation_content(
    tmp_path: Path,
    mutation: str,
) -> None:
    inputs = _create_gate_inputs(tmp_path)
    attestation = inputs["attestations"]["security"]
    if mutation == "schema":
        attestation["schema"] = "untrusted.test-output.v1"
    elif mutation == "check_id":
        attestation["check_id"] = "functional"
    elif mutation == "status":
        attestation["status"] = "failed"
    else:
        attestation["source_hashes"]["frontend-angular/a.ts"] = "0" * 64
    artifact = inputs["check_artifacts"]["security"]
    _write_json(artifact, attestation)
    release_checks = inputs["release_checks"]
    release_checks["checks"]["security"]["evidence_sha256"] = _sha256(artifact)
    _write_json(inputs["release_checks_path"], release_checks)

    with pytest.raises(GateInputError):
        _assemble(inputs)


@pytest.mark.parametrize(
    "unsafe_path",
    ["/tmp/check.json", "../check.json", "docs/check.json", "artifacts/test-gates/../../.env"],
)
def test_release_check_paths_are_stable_repository_relative_artifact_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    inputs = _create_gate_inputs(tmp_path)
    release_checks = inputs["release_checks"]
    release_checks["checks"]["functional"]["evidence_path"] = unsafe_path
    _write_json(inputs["release_checks_path"], release_checks)

    with pytest.raises(GateInputError):
        _assemble(inputs)


def test_assembler_rejects_unexpected_browser_payload_instead_of_copying_it(tmp_path: Path) -> None:
    inputs = _create_gate_inputs(tmp_path)
    measurements = inputs["measurements"]
    measurements["full_log"] = "repository full text and secret material"
    _write_json(inputs["measurements_path"], measurements)

    with pytest.raises(GateInputError, match="browser_measurements_unexpected_fields"):
        _assemble(inputs)


def test_final_evidence_cannot_claim_a_release_status_not_backed_by_attestation(tmp_path: Path) -> None:
    inputs = _create_gate_inputs(tmp_path)
    evidence = _assemble(inputs)
    evidence["checks"]["security"] = False

    with pytest.raises(GateInputError, match="release_check_status_mismatch:security"):
        _report(inputs, evidence)


def test_report_schema_validation_rejects_uncontracted_fields(tmp_path: Path) -> None:
    inputs = _create_gate_inputs(tmp_path)
    report = _report(inputs, _assemble(inputs))
    report["generated_at"] = "2026-07-18T12:00:00Z"

    with pytest.raises(GateInputError, match="report_contract_invalid"):
        _validate_report_schema(report)


def test_atomic_writer_preserves_previous_artifact_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "gate.json"
    output.write_text("previous\n", encoding="utf-8")

    def _fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr("scripts.run_codecompass_graph_visualization_gate.os.replace", _fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        _atomic_write_json(output, {"schema": "new"})

    assert output.read_text(encoding="utf-8") == "previous\n"
    assert list(tmp_path.glob(".gate.json.*.tmp")) == []


def test_failed_release_attestation_produces_failed_not_passing_report(tmp_path: Path) -> None:
    inputs = _create_gate_inputs(tmp_path)
    release_checks = deepcopy(inputs["release_checks"])
    attestation = deepcopy(inputs["attestations"]["production_build"])
    attestation["status"] = "failed"
    artifact = inputs["check_artifacts"]["production_build"]
    _write_json(artifact, attestation)
    release_checks["checks"]["production_build"]["status"] = "failed"
    release_checks["checks"]["production_build"]["evidence_sha256"] = _sha256(artifact)
    _write_json(inputs["release_checks_path"], release_checks)

    report = _report(inputs, _assemble(inputs))

    assert report["status"] == "failed"
    release_gate = next(gate for gate in report["gates"] if gate["gate_id"] == "release_checks")
    assert release_gate["measurements"]["production_build"] is False
