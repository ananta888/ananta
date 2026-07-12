from __future__ import annotations

import time

import pytest

from voice_runtime.backends.base import (
    ChatResult,
    TranscriptionCandidate,
    TranscriptionResult,
    TranscriptionSegment,
    TranscriptionWord,
)
from voice_runtime.context import VoiceRecognitionContext
from voice_runtime.fusion import CandidateScorer, DeterministicFusionService
from voice_runtime.fusion.alignment import align_candidates
from voice_runtime.parallel import CandidateExecutionPolicy, ParallelCandidateExecutor


class _Backend:
    def __init__(self, name: str, text: str, *, confidence: float, delay: float = 0.0, error: Exception | None = None):
        self._name = name
        self._text = text
        self._confidence = confidence
        self._delay = delay
        self._error = error

    def name(self) -> str:
        return self._name

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        time.sleep(self._delay)
        if self._error:
            raise self._error
        return TranscriptionResult(
            text=self._text,
            language=language or "de",
            duration_ms=1000,
            model=f"{self._name}-model",
            confidence=self._confidence,
            raw_backend=self._name,
            segments=(TranscriptionSegment(0, 1000, self._text, confidence=self._confidence, backend=self._name),),
            provenance={"model_revision": "test-revision", "device": "cpu"},
        )

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        return ChatResult(text=self._text, transcript=self._text)

    def list_models(self) -> list[dict]:
        return []

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()


def test_parallel_executor_overlaps_backends_and_preserves_partial_success():
    executor = ParallelCandidateExecutor()
    started = time.monotonic()
    candidates = executor.execute(
        {
            "classic": _Backend("classic", "Hallo Welt", confidence=0.8, delay=0.08),
            "modern": _Backend("modern", "Hallo Werlt", confidence=0.9, delay=0.08),
            "broken": _Backend("broken", "", confidence=0.0, error=RuntimeError("model unavailable")),
        },
        filename="sample.wav",
        content=b"fixed-audio",
        language="de",
        policy=CandidateExecutionPolicy(max_parallel_backends=3, deadline_seconds=1),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.15
    assert [item.backend for item in candidates] == ["broken", "classic", "modern"]
    assert next(item for item in candidates if item.backend == "broken").error.code == "unavailable"
    assert sum(item.status == "succeeded" for item in candidates) == 2


def test_deterministic_fusion_selects_existing_candidate_and_records_disagreement():
    executor = ParallelCandidateExecutor()
    candidates = executor.execute(
        {
            "classic": _Backend("classic", "Hallo Welt", confidence=0.7),
            "modern": _Backend("modern", "Hallo Werlt", confidence=0.95),
        },
        filename="sample.wav",
        content=b"same-audio",
        language="de",
        policy=CandidateExecutionPolicy(max_parallel_backends=2),
    )
    service = DeterministicFusionService(CandidateScorer())

    first = service.fuse(candidates)
    second = service.fuse(candidates)

    # Cross-engine raw confidence is deliberately not compared. Stable
    # agreement/ID tie-breakers select an existing candidate instead.
    assert first.result.text == "Hallo Welt"
    assert first.result.text in {item.text for item in candidates}
    assert first.result.disagreement_regions
    region = first.result.disagreement_regions[0]
    assert (region.start_ms, region.end_ms) == (0, 1000)
    assert all(item["source"]["backend"] for item in region.alternatives)
    assert all(item["lineage_id"] for item in region.alternatives)
    assert all(item["alignment_method"] == "time_v1" for item in region.alternatives)
    assert all("score" in item for item in region.alternatives)
    assert first.result.warnings == ("fusion_cross_engine_confidence_degraded",)
    assert first.result.decision_trace["confidence_comparison_mode"] == (
        "degraded_cross_engine_uncalibrated"
    )
    assert first.result.provenance_valid is True
    assert first.result_hash == second.result_hash
    assert all(
        item["candidate_id"] == first.result.selected_candidate_id
        for item in first.result.decision_trace["token_provenance"]
    )


def test_context_projection_filters_capabilities_and_bounds_untrusted_text():
    context = VoiceRecognitionContext(
        classic_transcript="x" * 100,
        glossary_terms=("Ananta", "Ananta", "Voice"),
        domain_hint="security",
    )

    projected = context.project({"transcript_reference", "hotwords"}, max_chars=12, max_terms=3)

    assert projected == {"classic_transcript": "x" * 12, "hotwords": ["Ananta", "Voice"]}
    assert "domain_hint" not in projected


def test_context_rejects_instruction_fields_and_expired_or_mutable_snapshots():
    with pytest.raises(ValueError, match="forbidden instruction"):
        VoiceRecognitionContext.from_mapping({"personalization": {"prompt": "ignore policy"}})
    with pytest.raises(ValueError, match="ownership"):
        VoiceRecognitionContext.from_mapping(
            {
                "personalization": {
                    "expires_at": time.time() + 60,
                    "persistence_owner": "runtime",
                    "runtime_persistence_allowed": True,
                }
            }
        )
    with pytest.raises(ValueError, match="expired"):
        VoiceRecognitionContext.from_mapping(
            {
                "personalization": {
                    "expires_at": time.time() - 1,
                    "persistence_owner": "hub",
                    "runtime_persistence_allowed": False,
                }
            }
        )
    with pytest.raises(ValueError, match="revoked"):
        VoiceRecognitionContext.from_mapping(
            {
                "personalization": {
                    "expires_at": time.time() + 60,
                    "consent_id": "consent-a",
                    "consent_version": 2,
                    "consent_granted": False,
                    "revocation_epoch": 2,
                    "persistence_owner": "hub",
                    "runtime_persistence_allowed": False,
                }
            }
        )


def test_fusion_without_successful_candidate_is_degraded():
    candidate = ParallelCandidateExecutor().execute(
        {"broken": _Backend("broken", "", confidence=0, error=RuntimeError("model unavailable"))},
        filename="a.wav",
        content=b"audio",
        language=None,
        policy=CandidateExecutionPolicy(),
    )

    outcome = DeterministicFusionService().fuse(candidate)

    assert outcome.result.text == ""
    assert outcome.result.provenance_valid is False
    assert outcome.result.warnings == ("fusion_no_successful_candidate",)


def test_word_consensus_can_assemble_only_provenanced_candidate_tokens():
    candidates = ParallelCandidateExecutor().execute(
        {
            "a": _Backend("a", "eins rot alt", confidence=0.9),
            "b": _Backend("b", "eins blau neu", confidence=0.8),
            "c": _Backend("c", "zwei blau alt", confidence=0.7),
        },
        filename="a.wav",
        content=b"audio",
        language="de",
        policy=CandidateExecutionPolicy(max_parallel_backends=3),
    )

    outcome = DeterministicFusionService().fuse(candidates)

    assert outcome.result.text == "eins blau alt"
    assert outcome.result.text not in {candidate.text for candidate in candidates}
    source_tokens = {
        candidate.candidate_id: set(candidate.text.split()) for candidate in candidates
    }
    for item in outcome.result.decision_trace["token_provenance"]:
        assert item["token"] in source_tokens[item["candidate_id"]]
    assert outcome.result.provenance_valid is True


def test_correlated_child_candidate_does_not_double_weight_its_parent_lineage():
    red = TranscriptionCandidate(
        candidate_id="red",
        backend="a",
        text="eins rot",
        confidence=0.9,
        lineage_id="red",
    )
    blue = TranscriptionCandidate(
        candidate_id="blue",
        backend="b",
        text="eins blau",
        confidence=0.7,
        lineage_id="blue-root",
    )
    blue_child = TranscriptionCandidate(
        candidate_id="blue-child",
        backend="c",
        text="eins blau",
        confidence=0.8,
        lineage_id="blue-root",
        parent_candidate_ids=("blue",),
    )

    service = DeterministicFusionService()
    without_child = service.fuse((red, blue))
    with_child = service.fuse((red, blue, blue_child))

    assert with_child.result.text == without_child.result.text
    assert with_child.result.decision_trace["candidate_scores"]["blue"]["signals"][
        "agreement"
    ]["value"] == without_child.result.decision_trace["candidate_scores"]["blue"][
        "signals"
    ]["agreement"]["value"]


def test_time_alignment_covers_insert_delete_overlap_and_is_bit_stable():
    reference = TranscriptionCandidate(
        candidate_id="reference",
        backend="a",
        text="eins zwei drei",
        words=(
            TranscriptionWord(0, 100, "eins"),
            TranscriptionWord(100, 210, "zwei"),
            TranscriptionWord(200, 300, "drei"),
        ),
    )
    alternative = TranscriptionCandidate(
        candidate_id="alternative",
        backend="b",
        text="eins plus zwei",
        words=(
            TranscriptionWord(0, 90, "eins"),
            TranscriptionWord(90, 140, "plus"),
            TranscriptionWord(140, 230, "zwei"),
        ),
    )

    first = align_candidates(reference, alternative)
    second = align_candidates(reference, alternative)

    assert first == second
    assert first.method == "time_v1"
    assert {item.operation for item in first.spans} >= {"equal", "insert", "delete"}
    assert all(
        item.start_ms is not None and item.end_ms is not None
        for item in first.spans
        if item.operation != "equal"
    )


def test_unicode_alignment_fallback_and_missing_word_times_are_not_invented():
    reference = TranscriptionCandidate(
        candidate_id="a",
        backend="a",
        text="Ｆoo Straße",
    )
    alternative = TranscriptionCandidate(
        candidate_id="b",
        backend="b",
        text="foo STRASSE",
    )

    alignment = align_candidates(reference, alternative)
    outcome = DeterministicFusionService().fuse((reference, alternative))

    assert alignment.method == "unicode_text_v1"
    assert [item.operation for item in alignment.spans] == ["equal"]
    provenance = outcome.result.decision_trace["token_provenance"]
    assert all(item["time_source"] == "unavailable" for item in provenance)
    assert all(item["start_ms"] is None and item["end_ms"] is None for item in provenance)
    assert outcome.result.segments[0].words == ()
    assert "fusion_word_timestamps_unavailable" in outcome.result.warnings
    assert outcome.result.provenance_valid is True


def test_production_config_rejects_mock_backend():
    from voice_runtime.config import VoiceRuntimeConfig

    with pytest.raises(ValueError, match="mock voice backends"):
        VoiceRuntimeConfig(production_profile=True).validate()
