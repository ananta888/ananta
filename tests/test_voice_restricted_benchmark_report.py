from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.release_report import (
    BenchmarkReportBuilder,
    ExecutionEvidence,
    ModelEvidence,
    ThresholdSet,
)

ROOT = Path(__file__).resolve().parents[1]


def _execution(*, profile_id: str = "cpu") -> ExecutionEvidence:
    return ExecutionEvidence(
        git_sha="a" * 40,
        engine_versions={"vosk": "0.3.45", "voice-runtime": "1.0"},
        profile_id=profile_id,
        hardware={"cpu": "fixture-cpu", "ram_mb": 16384, "gpu": None},
        configuration={"recognition_strategy": "parallel_fusion", "max_parallel_backends": 2},
        models=(
            ModelEvidence(
                capability="transcription",
                engine="vosk",
                model_id="vosk-de",
                model_revision="immutable-revision-1",
                manifest_digest="b" * 64,
                quantization="none",
                execution_location="voice-runtime",
            ),
        ),
    )


def _thresholds(name: str = "voice") -> ThresholdSet:
    raw = json.loads((ROOT / "benchmarks" / f"thresholds.{name}-core.v1.json").read_text(encoding="utf-8"))
    return ThresholdSet.from_mapping(raw)


def _metrics() -> dict[str, float]:
    return {
        "wer": 0.10,
        "cer": 0.05,
        "named_entity_accuracy": 0.95,
        "number_accuracy": 1.0,
        "timestamp_mae_ms": 80.0,
        "expected_calibration_error": 0.04,
        "brier_score": 0.05,
        "provenance_coverage": 1.0,
        "p95_latency_ms": 1000.0,
        "real_time_factor": 0.5,
        "peak_ram_mb": 512.0,
    }


def test_report_binds_revision_model_quantization_hardware_configuration_and_thresholds() -> None:
    report = BenchmarkReportBuilder().build(
        report_id="voice-holdout-run-1",
        suite_id="voice-core",
        dataset_id="voice-holdout-v1",
        dataset_digest="c" * 64,
        dataset_split="holdout",
        execution=_execution(),
        thresholds=_thresholds(),
        metrics=_metrics(),
        baseline_metrics={**_metrics(), "wer": 0.09},
        promotion_subject="fusion",
    )

    assert report.status == "passed"
    assert report.recommendation == "recommend_fusion"
    assert report.execution.models[0].quantization == "none"
    assert report.threshold_version == "voice-core-2026-07-12.v1"
    assert report.digest == report.digest
    assert json.dumps(report.as_dict(), sort_keys=True, allow_nan=False)


def test_report_fails_closed_for_regression_missing_metric_and_non_holdout_promotion() -> None:
    metrics = _metrics()
    metrics.pop("provenance_coverage")
    metrics["wer"] = 0.20
    report = BenchmarkReportBuilder().build(
        report_id="voice-ci-run-1",
        suite_id="voice-core",
        dataset_id="voice-ci-v1",
        dataset_digest="c" * 64,
        dataset_split="ci",
        execution=_execution(profile_id="ci-contract"),
        thresholds=_thresholds(),
        metrics=metrics,
        baseline_metrics={**_metrics(), "wer": 0.10},
        promotion_subject="enhancement",
    )

    assert report.status == "failed"
    assert {failure.reason_code for failure in report.failures} == {
        "metric_regression",
        "required_metric_missing",
        "holdout_required_for_promotion",
    }
    assert report.recommendation == "do_not_recommend_enhancement"


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ({"git_sha": "dirty"}, "git_sha"),
        ({"profile_id": "mystery-gpu"}, "profile"),
        ({"models": ()}, "model evidence"),
    ],
)
def test_execution_evidence_rejects_incomplete_or_unreproducible_identity(replacement, message) -> None:
    values = _execution().__dict__ | replacement
    with pytest.raises(ValueError, match=message):
        ExecutionEvidence(**values).validate()


def test_threshold_contract_rejects_nan_and_duplicate_metrics() -> None:
    raw = {
        "schema_version": "ananta.voice-restricted-thresholds.v1",
        "threshold_version": "bad-v1",
        "suite_id": "voice-core",
        "thresholds": [
            {"metric": "wer", "direction": "maximum", "limit": 0.2},
            {"metric": "wer", "direction": "maximum", "limit": 0.3},
        ],
    }
    with pytest.raises(ValueError, match="unique"):
        ThresholdSet.from_mapping(raw)
    raw["thresholds"] = [{"metric": "wer", "direction": "maximum", "limit": float("nan")}]
    with pytest.raises(ValueError, match="finite"):
        ThresholdSet.from_mapping(raw)


def test_report_evidence_rejects_secret_or_content_bearing_configuration() -> None:
    values = _execution().__dict__ | {
        "configuration": {
            "recognition_strategy": "single",
            "voice_personalization_encryption_key": "must-not-enter-evidence",
        }
    }
    with pytest.raises(ValueError, match="sensitive field"):
        ExecutionEvidence(**values).validate()


def test_benchmark_runner_writes_atomic_machine_readable_gate_evidence(tmp_path: Path) -> None:
    payload = {
        "report_id": "voice-runner-test",
        "suite_id": "voice-core",
        "dataset_id": "voice-holdout-v1",
        "dataset_digest": "c" * 64,
        "dataset_split": "holdout",
        "promotion_subject": "fusion",
        "execution": {
            **_execution().__dict__,
            "models": [model.__dict__ for model in _execution().models],
        },
        "metrics": _metrics(),
        "baseline_metrics": _metrics(),
    }
    input_path = tmp_path / "measurements.json"
    output_path = tmp_path / "report.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.run_release_benchmark",
            "--input",
            str(input_path),
            "--thresholds",
            str(ROOT / "benchmarks" / "thresholds.voice-core.v1.json"),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    evidence = json.loads(output_path.read_text(encoding="utf-8"))
    assert evidence["status"] == "passed"
    assert evidence["report_digest"].startswith("sha256:")
    schema = json.loads((ROOT / "benchmarks" / "release-report.schema.v1.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(evidence)
