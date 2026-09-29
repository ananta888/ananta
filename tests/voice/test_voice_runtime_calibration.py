from __future__ import annotations

import json

import pytest

from voice_runtime.backends.base import TranscriptionCandidate
from voice_runtime.fusion.consensus import DeterministicFusionService
from voice_runtime.fusion.scoring import (
    CalibrationEvaluation,
    CalibrationProfile,
    CandidateScorer,
    CandidateScoringSignals,
    VersionedSignal,
    load_calibration_profiles,
)


def _artifact(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "ananta.voice-calibration.v1",
                "profiles": [
                    {
                        "backend": "vosk",
                        "model_revision": "revision-1",
                        "dataset_version": "voice-ci-v1",
                        "calibrator_version": "linear-v1",
                        "slope": 0.5,
                        "intercept": 0.1,
                        "evaluation": {
                            "sample_count": 100,
                            "ece_before": 0.2,
                            "ece_after": 0.1,
                            "brier_before": 0.3,
                            "brier_after": 0.2,
                        },
                        "thresholds": {
                            "version": "voice-thresholds-v1",
                            "minimum_confidence": 0.6,
                            "language": "de",
                            "hardware_profile": "cpu",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_calibration_artifact_is_revision_bound_and_traced(tmp_path):
    scorer = CandidateScorer(load_calibration_profiles(_artifact(tmp_path)))
    candidate = TranscriptionCandidate(
        candidate_id="candidate-1",
        backend="vosk",
        model_revision="revision-1",
        text="Hallo",
        confidence=0.8,
        language="de",
        device="cpu",
    )

    _score, trace = scorer.score(candidate)

    assert trace["confidence"] == pytest.approx(0.5)
    assert trace["confidence_comparable"] is True
    assert trace["calibration"] == "voice-ci-v1"
    assert str(trace["calibration_digest"]).startswith("sha256:")
    assert trace["calibration_report"] == {
        "sample_count": 100,
        "ece_before": 0.2,
        "ece_after": 0.1,
        "brier_before": 0.3,
        "brier_after": 0.2,
    }
    assert trace["threshold_version"] == "voice-thresholds-v1"


def test_calibration_does_not_cross_model_revisions(tmp_path):
    scorer = CandidateScorer(load_calibration_profiles(_artifact(tmp_path)))
    candidate = TranscriptionCandidate(
        candidate_id="candidate-2",
        backend="vosk",
        model_revision="revision-2",
        text="Hallo",
        confidence=0.8,
    )

    _score, trace = scorer.score(candidate)

    assert trace["confidence"] == 0.8
    assert trace["calibration"] == "raw_uncalibrated"
    assert trace["signals"]["confidence"]["contribution"] == 0.0


def test_scoring_has_versioned_weights_and_missing_signals_never_gain_bonus(tmp_path):
    scorer = CandidateScorer(load_calibration_profiles(_artifact(tmp_path)))
    candidate = TranscriptionCandidate(
        candidate_id="candidate-signals",
        backend="vosk",
        model_revision="revision-1",
        text="Hallo",
        confidence=0.8,
        language="de",
        device="cpu",
    )
    agreement = VersionedSignal(
        value=0.75,
        version="agreement-fixture-v1",
        artifact_digest="sha256:" + "a" * 64,
    )

    first_score, first = scorer.score(
        candidate,
        signals=CandidateScoringSignals(agreement=agreement),
    )
    second_score, second = scorer.score(
        candidate,
        signals=CandidateScoringSignals(agreement=agreement),
    )

    assert (first_score, first) == (second_score, second)
    assert first["weights_version"] == "ananta.voice-candidate-weights.v1"
    assert set(first["weights"]) == {
        "confidence",
        "agreement",
        "glossary",
        "language_model",
        "audio_quality",
        "calibration_quality",
    }
    for name in ("glossary", "language_model", "audio_quality"):
        assert first["signals"][name]["available"] is False
        assert first["signals"][name]["contribution"] == 0.0


def test_calibration_scope_mismatch_degrades_instead_of_reusing_confidence(tmp_path):
    scorer = CandidateScorer(load_calibration_profiles(_artifact(tmp_path)))
    candidate = TranscriptionCandidate(
        candidate_id="candidate-wrong-hardware",
        backend="vosk",
        model_revision="revision-1",
        text="Hallo",
        confidence=0.99,
        language="en",
        device="cuda",
    )

    score, trace = scorer.score(candidate)

    assert score == 0.0
    assert trace["confidence_comparable"] is False
    assert trace["signals"]["confidence"]["reason"] == "calibration_missing"


def test_language_and_hardware_specific_profiles_can_share_model_revision(tmp_path):
    payload = json.loads(_artifact(tmp_path).read_text(encoding="utf-8"))
    english = {
        **payload["profiles"][0],
        "dataset_version": "voice-en-cuda-v1",
        "thresholds": {
            **payload["profiles"][0]["thresholds"],
            "version": "voice-en-cuda-thresholds-v1",
            "language": "en",
            "hardware_profile": "cuda",
        },
    }
    payload["profiles"].append(english)
    path = tmp_path / "scoped-calibration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    scorer = CandidateScorer(load_calibration_profiles(path))

    _score, trace = scorer.score(
        TranscriptionCandidate(
            candidate_id="candidate-en-cuda",
            backend="vosk",
            model_revision="revision-1",
            text="Hello",
            confidence=0.8,
            language="en",
            device="cuda",
        )
    )

    assert trace["calibration"] == "voice-en-cuda-v1"
    assert trace["threshold_version"] == "voice-en-cuda-thresholds-v1"


def test_partial_cross_engine_calibration_suppresses_all_confidence_comparison():
    profile = CalibrationProfile(
        backend="calibrated",
        model_revision="revision-1",
        dataset_version="dataset-v1",
        artifact_digest="sha256:" + "c" * 64,
        calibrator_version="linear-v1",
        evaluation=CalibrationEvaluation(100, 0.2, 0.1, 0.3, 0.2),
        threshold_version="threshold-v1",
        minimum_confidence=0.5,
    )
    candidates = (
        TranscriptionCandidate(
            candidate_id="a",
            backend="calibrated",
            model_revision="revision-1",
            text="eins rot",
            confidence=0.99,
        ),
        TranscriptionCandidate(
            candidate_id="b",
            backend="uncalibrated",
            model_revision="revision-2",
            text="eins blau",
            confidence=0.01,
        ),
    )

    outcome = DeterministicFusionService(
        CandidateScorer({("calibrated", "revision-1"): profile})
    ).fuse(candidates)

    traces = outcome.result.decision_trace["candidate_scores"]
    assert all(
        trace["signals"]["confidence"]["contribution"] == 0.0
        for trace in traces.values()
    )
    assert traces["a"]["signals"]["confidence"]["reason"] == (
        "calibration_set_incomplete"
    )
    assert outcome.result.confidence is None
    assert "fusion_cross_engine_confidence_degraded" in outcome.result.warnings
