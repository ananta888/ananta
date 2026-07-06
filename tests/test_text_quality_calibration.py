"""Kalibrations-Gates fuer das Textqualitaets-Subsystem.

Die Fixtures in tests/fixtures/text_quality/calibration/ sind handgepflegte,
deterministische Referenz-Beispiele ohne Timestamps oder IDs. Sie definieren
Score-Baender (keine exakten Zahlen) und getrennte Akzeptanz-Gates fuer
False-Positive- und False-Negative-Schranken sowie Cross-Sprache-Isolation.

Upstream- oder Profilaenderungen erfordern explizites Snapshot-Review.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.services.text_quality.deterministic_scanner import DeterministicTextQualityScanner
from agent.services.text_quality.evaluator_service import TextQualityEvaluatorService
from agent.services.text_quality.models import (
    ContentKind,
    KNOWN_REASON_CODES,
    TextQualityEvaluationRequest,
)
from agent.services.text_quality.score_fusion_policy import ScoreFusionPolicy

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "text_quality" / "calibration"

ENGLISH_PROFILE_PHRASES = {"in today's world", "it is important to note", "let's delve into", "moreover"}


def _load(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    return payload


def _build_request(text: str, language: str, content_kind: str | None) -> TextQualityEvaluationRequest:
    """Baut einen Request gegen das *aktive* Profil (Locale-natives JSON)."""

    kind = ContentKind(content_kind) if content_kind else ContentKind.FREEFORM_PROSE
    profile_path = Path("agent/services/text_quality/profiles") / f"{language}.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    from agent.services.text_quality.models import CriteriaSet

    criteria = CriteriaSet(
        version=str(profile["version"]),
        language=language,
        profile_name=str(profile["profile_name"]),
        content_kinds=[kind],
        status="enabled",
        blocked_phrases=list(profile["blocked_phrases"]),
        thresholds=dict(profile["thresholds"]),
    )
    return TextQualityEvaluationRequest(
        text=text,
        language=language,
        content_kind=kind,
        criteria=criteria,
    )


def _scanner_signal(request: TextQualityEvaluationRequest):
    return DeterministicTextQualityScanner().analyze(request)


def test_unknown_reason_codes_in_fixtures_are_valid():
    """Schutz gegen Drift: Fixtures duerfen keine unbekannten Reason-Codes benennen."""

    seen: set[str] = set()
    for path in FIXTURE_DIR.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for example in payload.get("examples", []):
            for code in list(example.get("reason_codes_min") or []) + list(example.get("forbidden_reason_codes") or []):
                seen.add(code)
    assert seen, "no calibration fixtures found"
    unknown = seen - KNOWN_REASON_CODES
    assert not unknown, f"unknown_reason_codes_in_fixtures:{sorted(unknown)}"


def test_german_slop_examples_hit_minimum_score_and_required_reason_codes():
    payload = _load("de_slop.json")
    min_score = float(payload["thresholds"]["min_slop_score"])
    service = TextQualityEvaluatorService(fusion=ScoreFusionPolicy())
    for example in payload["examples"]:
        text = str(example["text"])
        assert len(text.split()) >= 10, f"fixture_too_short:{example['id']}"
        result = service.evaluate(_build_request(text, "de", "freeform_prose"))
        assert result.slop_score >= min_score, (
            f"{example['id']}: slop {result.slop_score} < {min_score}"
        )
        for code in list(example.get("reason_codes_min") or []):
            assert code in result.reason_codes, (
                f"{example['id']}: expected reason {code} missing (got {result.reason_codes})"
            )


def test_german_concrete_examples_remain_below_false_positive_ceiling():
    payload = _load("de_concrete.json")
    max_score = float(payload["thresholds"]["max_slop_score"])
    for example in payload["examples"]:
        kind = str(example.get("content_kind") or "technical_documentation")
        result = DeterministicTextQualityScanner().analyze(
            _build_request(str(example["text"]), "de", kind)
        )
        normalized = result.normalized_signal_score
        assert normalized <= max_score, (
            f"{example['id']}: false_positive {normalized} > {max_score}"
        )
        for code in list(example.get("forbidden_reason_codes") or []):
            assert code not in result.reason_codes, (
                f"{example['id']}: forbidden reason {code} present"
            )


def test_english_slop_examples_hit_required_reason_codes():
    payload = _load("en_slop.json")
    min_score = float(payload["thresholds"]["min_slop_score"])
    for example in payload["examples"]:
        text = str(example["text"])
        result = DeterministicTextQualityScanner().analyze(
            _build_request(text, "en", "freeform_prose")
        )
        assert result.normalized_signal_score >= min_score, (
            f"{example['id']}: slop {result.normalized_signal_score} < {min_score}"
        )
        for code in list(example.get("reason_codes_min") or []):
            assert code in result.reason_codes, (
                f"{example['id']}: expected reason {code} missing"
            )


def test_english_concrete_examples_remain_below_false_positive_ceiling():
    payload = _load("en_concrete.json")
    max_score = float(payload["thresholds"]["max_slop_score"])
    for example in payload["examples"]:
        result = DeterministicTextQualityScanner().analyze(
            _build_request(str(example["text"]), "en", "technical_documentation")
        )
        assert result.normalized_signal_score <= max_score, (
            f"{example['id']}: false_positive {result.normalized_signal_score} > {max_score}"
        )
        for code in list(example.get("forbidden_reason_codes") or []):
            assert code not in result.reason_codes, (
                f"{example['id']}: forbidden reason {code} present"
            )


def test_english_profile_phrases_have_zero_weight_in_german_profile():
    """Cross-Sprache-Isolation: im deutschen Profil duerfen englische Phrasen
    *nicht* ueber das de-Profil als Slop-Signal auftauchen, solange keine
    separate deutsche Bestätigung existiert."""

    payload = _load("cross_language_isolation.json")
    for example in payload["examples"]:
        text = str(example["text"])
        scanner_signal = _scanner_signal(_build_request(text, "de", "freeform_prose"))
        english_only_findings = [
            finding for finding in scanner_signal.findings
            if any(phrase in text.lower() for phrase in ENGLISH_PROFILE_PHRASES)
            and finding.reason_code in {"generic_phrase", "overused_transition"}
        ]
        assert not english_only_findings, (
            f"{example['id']}: english-only findings leaked into de profile: "
            f"{english_only_findings}"
        )


def test_score_fusion_weight_sum_after_provider_degradation_remains_stable():
    """Wenn ein Provider degraded oder wegfällt, darf die gewichtete Fusion
    nicht zu Score 0 oder zu Identitaetsverlust fuehren. Verifikation
    ueber normalisierte Gewichte."""

    from agent.services.text_quality.models import DetectorSignal, EvaluationStatus

    fusion = ScoreFusionPolicy()
    completed = DetectorSignal(
        provider_name="core_scanner",
        provider_version="test",
        confidence=0.8,
        normalized_signal_score=0.5,
        status=EvaluationStatus.COMPLETED,
    )
    score_a, _, _ = fusion.fuse([completed])
    score_b, _, _ = fusion.fuse([
        completed,
        DetectorSignal(
            provider_name="avoid_ai_writing",
            provider_version="test",
            confidence=0.0,
            normalized_signal_score=0.0,
            status=EvaluationStatus.DEGRADED,
            degraded_reason="provider_unavailable",
        ),
    ])
    assert score_a == pytest.approx(score_b, abs=1e-6)
    score_c, _, _ = fusion.fuse([
        completed,
        DetectorSignal(
            provider_name="avoid_ai_writing",
            provider_version="test",
            confidence=0.5,
            normalized_signal_score=0.4,
            status=EvaluationStatus.UNSCORABLE,
        ),
    ])
    assert score_c == pytest.approx(score_a, abs=1e-6)


def test_short_text_stylometric_signal_is_not_invented():
    """Stylometrische Mindestlaenge: zu kurze Texte muessen unscorable sein,
    damit style_fit_score nicht aus einem Einzeltext erfunden wird."""

    signal = _scanner_signal(_build_request("Kurz.", "de", "freeform_prose"))
    assert signal.status.value == "unscorable"
    assert "text_too_short" in signal.reason_codes
