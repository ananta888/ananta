from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.services.semantic_media_program_evidence import ProgramEvidenceError
from scripts.benchmark.peer_speech_evidence_sync import (
    BenchmarkMode,
    benchmark_binding,
    evaluate,
    evaluate_measurement_document,
    execute_measurement_document,
    measurement_run_id,
)

ROOT = Path(__file__).resolve().parents[1]


def test_peer_enrichment_benchmark_blocks_leakage_quality_and_p95_regressions() -> None:
    passing = evaluate(
        (
            BenchmarkMode("local_only", 180_000, 0, 100),
            BenchmarkMode("peer_enriched", 160_000, 0, 105),
            BenchmarkMode("blind_merge", 300_000, 2, 120),
        )
    )
    assert passing["passed"] is True
    assert passing["quality_claim"] is False

    failing = evaluate(
        (
            BenchmarkMode("local_only", 180_000, 0, 100),
            BenchmarkMode("peer_enriched", 190_000, 1, 106),
            BenchmarkMode("blind_merge", 300_000, 2, 120),
        )
    )
    assert failing["passed"] is False
    assert set(failing["reason_codes"]) == {
        "speech_sync_leakage_detected",
        "speech_sync_quality_regression",
        "speech_sync_transcript_p95_regression",
    }


def _measurements() -> dict[str, object]:
    source_digest, config_digest = benchmark_binding()
    modes = [
        {
            "mode": "local_only",
            "lexical_error_micros": 180_000,
            "leakage_events": 0,
            "transcript_p95_ms": 100,
            "sample_count": 30,
        },
        {
            "mode": "peer_enriched",
            "lexical_error_micros": 160_000,
            "leakage_events": 0,
            "transcript_p95_ms": 105,
            "sample_count": 30,
        },
        {
            "mode": "blind_merge",
            "lexical_error_micros": 300_000,
            "leakage_events": 2,
            "transcript_p95_ms": 120,
            "sample_count": 30,
        },
    ]
    return {
        "schema": "ananta.peer-speech-sync-measurements.v1",
        "source_sha256": source_digest,
        "config_sha256": config_digest,
        "run_id_sha256": measurement_run_id(
            source_sha256=source_digest,
            config_sha256=config_digest,
            modes=modes,
        ),
        "modes": modes,
    }


def test_peer_sync_benchmark_requires_explicit_measurements() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/benchmark/peer_speech_evidence_sync.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert completed.returncode != 0
    assert report["status"] == "unverified"
    assert report["release_blocking"] is True
    assert report["reason_codes"] == ["speech_sync_measurements_not_supplied"]


def test_peer_sync_benchmark_emits_source_and_config_bound_gate_evidence() -> None:
    evidence = evaluate_measurement_document(_measurements())
    expected_source, expected_config = benchmark_binding()
    assert evidence.status == "passed"
    assert evidence.source_sha256 == expected_source
    assert evidence.config_sha256 == expected_config
    assert evidence.measurements["peer_sample_count"] == 30
    assert evidence.measurements["quality_claim"] is False


@pytest.mark.parametrize(
    ("field", "reason"),
    (
        ("source_sha256", "speech_sync_measurement_source_stale"),
        ("config_sha256", "speech_sync_measurement_config_stale"),
    ),
)
def test_peer_sync_benchmark_rejects_stale_measurement_bindings(field: str, reason: str) -> None:
    document = _measurements()
    document[field] = "0" * 64
    with pytest.raises(ProgramEvidenceError, match=reason):
        evaluate_measurement_document(document)


def test_peer_sync_benchmark_rejects_rows_not_bound_to_run_identity() -> None:
    document = _measurements()
    document["modes"][1]["transcript_p95_ms"] = 104
    with pytest.raises(ProgramEvidenceError, match="speech_sync_measurement_run_binding_mismatch"):
        evaluate_measurement_document(document)


def test_peer_sync_benchmark_executes_real_components_with_content_free_output() -> None:
    document = execute_measurement_document()
    evidence = evaluate_measurement_document(document)

    assert document["run_id_sha256"] == evidence.measurements["measurement_run_id_sha256"]
    assert all(row["sample_count"] >= 30 for row in document["modes"])
    assert all(row["leakage_events"] == 0 for row in document["modes"])
    assert "privatecanary" not in json.dumps(document)
    assert evidence.status in {"passed", "failed"}
    assert evidence.measurements["quality_claim"] is False


def test_peer_sync_benchmark_execute_cli_emits_verified_gate_evidence() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/benchmark/peer_speech_evidence_sync.py", "--execute"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert completed.returncode in {0, 1}
    assert report["status"] in {"passed", "failed"}
    assert report["release_blocking"] is (report["status"] != "passed")
    assert report["measurements"]["peer_sample_count"] >= 30
