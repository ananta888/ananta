from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.dataset_contract import CalibrationArtifact, load_dataset_manifest
from benchmarks.restricted_inference.metrics import classification_metrics, ranking_metrics
from benchmarks.voice.metrics import brier_score, evaluate_voice_sample, expected_calibration_error

ROOT = Path(__file__).resolve().parents[1]


def test_dataset_manifests_are_versioned_and_provenanced():
    voice = load_dataset_manifest(ROOT / "benchmarks/voice/datasets.v1.json")
    restricted = load_dataset_manifest(ROOT / "benchmarks/restricted_inference/datasets.v1.json")

    assert voice[0].split == "ci"
    assert restricted[0].license == "Apache-2.0"


def test_voice_metrics_cover_quality_calibration_and_resources():
    result = evaluate_voice_sample(
        reference="Ananta hat 42 Tasks",
        hypothesis="Ananta hat 43 Tasks",
        reference_entities=["Ananta"],
        hypothesis_entities=["Ananta"],
        latency_ms=500,
        audio_duration_ms=1_000,
        reference_timestamps=((0, 200), (200, 400), (400, 600), (600, 800)),
        hypothesis_timestamps=((10, 210), (190, 410), (420, 620)),
        missing_timestamp_penalty_ms=500,
        total_tokens=4,
        provenanced_tokens=4,
        peak_ram_mb=128,
    )

    assert result.wer == pytest.approx(0.25)
    assert result.named_entity_accuracy == 1
    assert result.number_accuracy == 0
    assert result.real_time_factor == 0.5
    assert result.timestamp_mae_ms == pytest.approx(135)
    assert result.provenance_coverage == 1.0
    assert expected_calibration_error([0.9, 0.2], [True, False]) == pytest.approx(0.15)
    assert brier_score([0.9, 0.2], [True, False]) == pytest.approx(0.025)


def test_restricted_metrics_are_deterministic():
    classification = classification_metrics(["a", "b", "a"], ["a", "b", "b"])
    ranking = ranking_metrics([{"x"}, {"b"}], [["x", "y"], ["a", "b"]])

    assert classification["accuracy"] == pytest.approx(2 / 3)
    assert 0 < classification["macro_f1"] < 1
    assert ranking["mrr"] == pytest.approx(0.75)


def test_calibration_artifact_is_revision_and_dataset_bound():
    artifact = CalibrationArtifact.build(
        backend="vosk",
        model_revision="sha256:model",
        dataset_version="voice-ci-v1",
        points=((0.0, 0.1), (0.5, 0.6), (1.0, 0.9)),
    )

    assert artifact.calibrate(
        0.25,
        backend="vosk",
        model_revision="sha256:model",
        dataset_version="voice-ci-v1",
    ) == pytest.approx(0.35)
    with pytest.raises(ValueError, match="identity mismatch"):
        artifact.calibrate(0.5, backend="vosk", model_revision="other", dataset_version="voice-ci-v1")
