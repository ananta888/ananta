"""Pure transcription-result transformations used by the pipeline.

These helpers own no execution state: they reshape, merge and annotate
``TranscriptionResult`` values so the orchestrator and its rerun stages share
one deterministic implementation.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

from .backends.base import (
    TranscriptionCandidate,
    TranscriptionResult,
    TranscriptionSegment,
    TranscriptionWord,
)
from .fusion import CalibrationProfile, CandidateScorer


def stable_candidate_id(backend_id: str, source_audio_id: str, variant: str) -> str:
    digest = hashlib.sha256(
        backend_id.encode("utf-8")
        + b"\0"
        + variant.encode("utf-8")
        + b"\0"
        + source_audio_id.encode("utf-8")
    ).hexdigest()
    return f"candidate-{backend_id}-{digest[:16]}"


def result_from_candidate(candidate: TranscriptionCandidate) -> TranscriptionResult:
    return TranscriptionResult(
        text=candidate.text,
        language=candidate.language,
        duration_ms=candidate.duration_ms,
        model=candidate.model,
        warnings=candidate.warnings,
        segments=candidate.segments,
        confidence=candidate.confidence,
        raw_backend=candidate.backend,
        provenance=dict(candidate.provenance),
    )


def ensure_metadata(
    result: TranscriptionResult,
    *,
    pipeline: str,
    stages: tuple[dict, ...],
) -> TranscriptionResult:
    segments = result.segments
    if not segments and result.text:
        segments = (
            TranscriptionSegment(
                start_ms=0,
                end_ms=result.duration_ms or max(50, len(result.text) * 2),
                text=result.text,
                confidence=result.confidence,
                backend=result.raw_backend or result.model,
            ),
        )
    confidences = [segment.confidence for segment in segments if segment.confidence is not None]
    confidence = result.confidence
    if confidence is None and confidences:
        confidence = sum(confidences) / len(confidences)
    return replace(
        result,
        segments=segments,
        pipeline=result.pipeline or pipeline,
        confidence=confidence,
        raw_backend=result.raw_backend or result.model,
        stages=tuple([*result.stages, *stages]),
    )


def shift_result(result: TranscriptionResult, *, offset_ms: int) -> TranscriptionResult:
    if offset_ms == 0:
        return result
    shifted_segments = tuple(
        replace(
            segment,
            start_ms=segment.start_ms + offset_ms,
            end_ms=segment.end_ms + offset_ms,
            words=tuple(
                replace(word, start_ms=word.start_ms + offset_ms, end_ms=word.end_ms + offset_ms)
                for word in segment.words
            ),
        )
        for segment in result.segments
    )
    return replace(
        result,
        duration_ms=(result.duration_ms + offset_ms) if result.duration_ms is not None else None,
        segments=shifted_segments,
    )


def regional_replacement(
    result: TranscriptionResult,
    start_ms: int,
    end_ms: int,
    backend_id: str,
) -> TranscriptionSegment:
    words = tuple(
        TranscriptionWord(
            start_ms=max(start_ms, min(end_ms, start_ms + word.start_ms)),
            end_ms=max(start_ms, min(end_ms, start_ms + word.end_ms)),
            text=word.text,
            confidence=word.confidence,
            candidate_id=word.candidate_id,
        )
        for segment in result.segments
        for word in segment.words
        if word.text
    )
    return TranscriptionSegment(
        start_ms=start_ms,
        end_ms=end_ms,
        text=result.text.strip(),
        confidence=result.confidence,
        backend=backend_id,
        warnings=("adaptive_regional_rerun_applied",),
        words=words,
    )


def merge_results(
    *,
    results: list[TranscriptionResult],
    filename: str,
    fallback_language: str | None,
    default_model: str,
) -> TranscriptionResult:
    if not results:
        return TranscriptionResult(
            text="",
            language=fallback_language or "und",
            model=default_model,
            warnings=("pipeline_no_segments",),
        )
    warnings: list[str] = []
    segments: list[TranscriptionSegment] = []
    offset_ms = 0
    for result in results:
        warnings.extend(result.warnings)
        if result.segments:
            segments.extend(result.segments)
        else:
            duration = result.duration_ms or max(50, len(result.text) * 2)
            segments.append(
                TranscriptionSegment(
                    start_ms=offset_ms,
                    end_ms=offset_ms + duration,
                    text=result.text,
                    confidence=result.confidence,
                    backend=result.raw_backend or result.model,
                )
            )
        offset_ms = max(offset_ms, result.duration_ms or 0)
    text = " ".join(segment.text for segment in segments if segment.text).strip()
    confidences = [segment.confidence for segment in segments if segment.confidence is not None]
    return TranscriptionResult(
        text=text or f"transcript ({filename or 'audio'})",
        language=results[0].language or fallback_language or "und",
        duration_ms=max((segment.end_ms for segment in segments), default=results[0].duration_ms),
        model=results[0].model or default_model,
        warnings=tuple(warnings),
        segments=tuple(segments),
        confidence=(sum(confidences) / len(confidences)) if confidences else results[0].confidence,
        raw_backend=results[0].raw_backend or results[0].model,
    )


def calibration_for_result(
    candidate_scorer: CandidateScorer,
    result: TranscriptionResult,
) -> CalibrationProfile | None:
    candidate: TranscriptionCandidate | None = None
    if result.selected_candidate_id:
        candidate = next(
            (item for item in result.candidates if item.candidate_id == result.selected_candidate_id),
            None,
        )
    if candidate is None:
        candidate = TranscriptionCandidate.from_result(
            candidate_id="adaptive-routing-baseline",
            backend=result.raw_backend or "unknown",
            result=result,
        )
    profile = candidate_scorer.calibration_profile(candidate)
    return profile if profile is not None and profile.comparable else None
