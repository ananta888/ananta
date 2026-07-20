#!/usr/bin/env python3
"""Measure deterministic reconstruction, linkability and membership attacks.

This is a bounded synthetic speech-like attack corpus, not a claim of formal
anonymity. The report contains aggregate measurements only. A failed privacy
threshold is a successful conformance result only when production remains
fail-closed for the measured adapter version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
from array import array
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/acoustic-residual-privacy.json"
SOURCE_FILES = (
    "docs/security/acoustic-residual-privacy.md",
    "scripts/benchmark/acoustic_residual_privacy.py",
    "voice_runtime/features/residual.py",
)
THRESHOLDS = {
    "maximum_reconstructability_score": 0.30,
    "maximum_speaker_linkability_score": 0.25,
    "maximum_membership_inference_score": 0.15,
}
FORBIDDEN_FIELDS = frozenset(
    {"audio", "ciphertext", "features", "pcm", "samples", "transcript", "waveform"}
)


def _source_hash() -> str:
    digest = hashlib.sha256()
    for relative in SOURCE_FILES:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((ROOT / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise RuntimeError("residual_attack_dimension_mismatch")
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)) / len(left))


def _correlation_squared(left: Sequence[float], right: Sequence[float]) -> float:
    mean_left = statistics.fmean(left)
    mean_right = statistics.fmean(right)
    covariance = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True))
    variance_left = sum((value - mean_left) ** 2 for value in left)
    variance_right = sum((value - mean_right) ** 2 for value in right)
    return covariance**2 / (variance_left * variance_right) if variance_left and variance_right else 0.0


def _speech_like_values(profile: int, utterance: int) -> array:
    sample_rate = 16_000
    length = 16_000
    generator = random.Random(137 + profile * 1_009 + utterance * 37)
    frequency = 105 + profile * 31
    phase = generator.random() * 2 * math.pi
    amplitude = 0.22 + generator.random() * 0.14
    modulation = 0.7 + generator.random() * 2
    values = array("h")
    for index in range(length):
        envelope = amplitude * (0.8 + 0.2 * math.sin(2 * math.pi * modulation * index / sample_rate))
        value = envelope * math.sin(2 * math.pi * frequency * index / sample_rate + phase)
        value += generator.gauss(0, 0.012)
        values.append(max(-32_768, min(32_767, round(value * 32_767))))
    return values


def _corpus():
    from voice_runtime.features.residual import AcousticResidualAdapter

    rows = []
    for profile in range(8):
        for utterance in range(12):
            values = _speech_like_values(profile, utterance)
            residual = AcousticResidualAdapter().extract(
                grant_active=True,
                pcm_s16le=values.tobytes(),
                sample_rate_hz=16_000,
            )
            if len(residual) != 128:
                raise RuntimeError("residual_attack_dimension_mismatch")
            rows.append((profile, utterance, values, residual))
    return rows


def _reconstruction_attack(rows) -> float:
    scores = []
    for _profile, _utterance, values, residual in rows:
        block = math.ceil(len(values) / len(residual))
        # Public zero-phase envelope attack: expand every disclosed block RMS
        # into a constant-amplitude block and measure explained variance.
        reconstruction = [value * 32_768 for value in residual for _ in range(block)][: len(values)]
        scores.append(_correlation_squared(values, reconstruction))
    return max(scores)


def _centroids(rows) -> dict[int, tuple[float, ...]]:
    return {
        profile: tuple(
            statistics.fmean(
                row[3][index]
                for row in rows
                if row[0] == profile and row[1] < 6
            )
            for index in range(128)
        )
        for profile in range(8)
    }


def _speaker_attack(rows, centroids: dict[int, tuple[float, ...]]) -> float:
    evaluated = [(profile, residual) for profile, utterance, _values, residual in rows if utterance >= 6]
    correct = sum(
        min(centroids, key=lambda candidate: _distance(residual, centroids[candidate])) == profile
        for profile, residual in evaluated
    )
    accuracy = correct / len(evaluated)
    chance = 1 / len(centroids)
    return max(0.0, (accuracy - chance) / (1 - chance))


def _membership_attack(rows, centroids: dict[int, tuple[float, ...]]) -> float:
    enrolled = tuple(centroids[index] for index in range(4))
    scored = [
        (-min(_distance(residual, centroid) for centroid in enrolled), profile < 4)
        for profile, utterance, _values, residual in rows
        if utterance >= 6
    ]
    positives = sum(label for _score, label in scored)
    negatives = len(scored) - positives
    advantages = []
    for threshold in sorted({score for score, _label in scored}):
        true_positive = sum(score >= threshold and label for score, label in scored) / positives
        false_positive = sum(score >= threshold and not label for score, label in scored) / negatives
        advantages.append(true_positive - false_positive)
    return max(advantages, default=1.0)


def _assert_content_free(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_FIELDS:
                raise RuntimeError(f"residual_privacy_forbidden_field:{key}")
            _assert_content_free(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_content_free(nested)


@lru_cache(maxsize=1)
def expected_document() -> dict[str, Any]:
    from voice_runtime.features.residual import (
        MEASURED_PRIVACY_GATE_VERSION,
        MEASURED_PRIVACY_VERDICT,
    )

    rows = _corpus()
    centroids = _centroids(rows)
    measurements = {
        "reconstructability_score": round(_reconstruction_attack(rows), 6),
        "speaker_linkability_score": round(_speaker_attack(rows, centroids), 6),
        "membership_inference_score": round(_membership_attack(rows, centroids), 6),
    }
    activation_allowed = (
        measurements["reconstructability_score"] <= THRESHOLDS["maximum_reconstructability_score"]
        and measurements["speaker_linkability_score"] <= THRESHOLDS["maximum_speaker_linkability_score"]
        and measurements["membership_inference_score"] <= THRESHOLDS["maximum_membership_inference_score"]
    )
    measured_verdict = "go" if activation_allowed else "no_go"
    checks = {
        "bounded_corpus_complete": len(rows) == 96,
        "measurements_finite": all(math.isfinite(value) and 0 <= value <= 1 for value in measurements.values()),
        "production_policy_matches_measurement": MEASURED_PRIVACY_VERDICT == measured_verdict,
        "failed_threshold_disables_activation": activation_allowed or MEASURED_PRIVACY_VERDICT == "no_go",
    }
    document = {
        "schema_version": "ananta.acoustic-residual-privacy-gate.v1",
        "gate": "ASMP-SPR-006",
        "source_sha256": _source_hash(),
        "source_files": list(SOURCE_FILES),
        "calibration_version": MEASURED_PRIVACY_GATE_VERSION,
        "corpus": {
            "generator": "deterministic-speech-like-v1",
            "profile_count": 8,
            "utterance_count": 96,
            "sample_rate_hz": 16_000,
            "duration_ms_per_utterance": 1_000,
            "train_utterances_per_profile": 6,
            "test_utterances_per_profile": 6,
            "production_population_claim": False,
        },
        "thresholds": THRESHOLDS,
        "measurements": measurements,
        "decision": {
            "measured_verdict": measured_verdict,
            "production_policy_verdict": MEASURED_PRIVACY_VERDICT,
            "activation_allowed": activation_allowed,
            "ordinary_and_transcript_fallback_required": True,
        },
        "checks": checks,
        "content_policy": {"content_free": True, "forbidden_fields": sorted(FORBIDDEN_FIELDS)},
        "passed": all(checks.values()),
    }
    _assert_content_free(document)
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    document = expected_document()
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.verify and (not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered):
        raise SystemExit("acoustic_residual_privacy_report_stale")
    if not args.write and not args.verify:
        print(rendered, end="")
    return 0 if document["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
