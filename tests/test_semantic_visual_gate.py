from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

from agent.services.semantic_media_program_evidence import GateEvidence, canonical_sha256, source_hash
from scripts.e2e.semantic_media_e2e_report import playwright_gate_config
from scripts.run_semantic_visual_gate import (
    VISUAL_LIFECYCLE_SOURCE_PATHS,
    VISUAL_LIFECYCLE_SPEC,
    evaluate_visual_gate,
)

ROOT = Path(__file__).resolve().parents[1]


def test_visual_gate_is_directly_executable_without_custom_pythonpath() -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "scripts/run_semantic_visual_gate.py", "--help"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--lifecycle-e2e" in completed.stdout


def _lifecycle_evidence() -> dict:
    return GateEvidence(
        gate_id="ASMP-VIS-012",
        status="passed",
        reason_codes=(),
        source_sha256=source_hash(ROOT, VISUAL_LIFECYCLE_SOURCE_PATHS),
        config_sha256=canonical_sha256(playwright_gate_config(spec=VISUAL_LIFECYCLE_SPEC)),
        measurements={
            "browser_count": 2,
            "visual_lifecycle_scenario_count": 2,
            "visual_process_count": 2,
            "visual_engine_count": 2,
            "visual_scenario_min": 6,
            "visual_observe_min": 12,
            "visual_active_min": 12,
            "visual_recovery_min": 12,
            "visual_revoke_min": 12,
            "visual_reconnect_min": 12,
            "visual_ordinary_fallback_min": 12,
            "visual_direct_link_min": 2,
            "visual_ordinary_receiver_min": 2,
        },
    ).as_document()


def test_current_no_go_cannot_be_relabelled_and_preserves_ordinary() -> None:
    spike = json.loads((ROOT / "artifacts/domain/semantic-visual-feasibility.json").read_text())
    benchmark = json.loads((ROOT / "artifacts/domain/semantic-visual-benchmark.json").read_text())
    result = evaluate_visual_gate(spike, benchmark, _lifecycle_evidence())
    assert not result["passed"]
    assert not result["semantic_visual_activation"]
    assert result["ordinary_fallback_required"]
    assert result["lifecycle_e2e_passed"]
    assert "spike_no_go" in result["reasons"]
    assert "benchmark_byte_benefit_missing" in result["reasons"]
    assert result["benchmark_thresholds"]["maximum_mean_byte_ratio"] == 0.7


def test_gate_fails_closed_for_incomplete_benchmark_even_with_claimed_go() -> None:
    result = evaluate_visual_gate(
        {"decision": {"verdict": "go", "activation_allowed": True}, "thresholds": {}},
        {"schema": "ananta.semantic-visual-benchmark.v1"},
    )
    assert not result["passed"]
    assert "benchmark_matrix_incomplete" in result["reasons"]


def test_claimed_go_cannot_override_recomputed_no_go_measurements() -> None:
    spike = json.loads((ROOT / "artifacts/domain/semantic-visual-feasibility.json").read_text())
    benchmark = json.loads((ROOT / "artifacts/domain/semantic-visual-benchmark.json").read_text())
    forged = copy.deepcopy(spike)
    forged["decision"]["verdict"] = "go"
    forged["decision"]["activation_allowed"] = True
    result = evaluate_visual_gate(forged, benchmark)
    assert not result["passed"]
    assert "spike_decision_inconsistent" in result["reasons"]
    assert "spike_no_go" in result["reasons"]


def test_lifecycle_evidence_is_source_bound_and_required() -> None:
    spike = json.loads((ROOT / "artifacts/domain/semantic-visual-feasibility.json").read_text())
    benchmark = json.loads((ROOT / "artifacts/domain/semantic-visual-benchmark.json").read_text())
    missing = evaluate_visual_gate(spike, benchmark)
    assert not missing["lifecycle_e2e_passed"]
    assert "visual_lifecycle_e2e_missing" in missing["reasons"]

    stale = _lifecycle_evidence()
    stale["source_sha256"] = "0" * 64
    invalid = evaluate_visual_gate(spike, benchmark, stale)
    assert not invalid["lifecycle_e2e_passed"]
    assert "gate_report_source_stale" in invalid["reasons"]
