from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class TranscriptionWord:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    candidate_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "confidence": self.confidence,
            "candidate_id": self.candidate_id,
        }


@dataclass(frozen=True)
class TranscriptionSegment:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    speaker: str | None = None
    backend: str | None = None
    warnings: tuple[str, ...] = ()
    candidate_id: str | None = None
    words: tuple[TranscriptionWord, ...] = ()

    def as_dict(self) -> dict:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "confidence": self.confidence,
            "speaker": self.speaker,
            "backend": self.backend,
            "warnings": list(self.warnings),
            "candidate_id": self.candidate_id,
            "words": [word.as_dict() for word in self.words],
        }


@dataclass(frozen=True)
class CandidateError:
    code: str
    message: str
    retriable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retriable": self.retriable}


@dataclass(frozen=True)
class TranscriptionCandidate:
    candidate_id: str
    backend: str
    model: str | None = None
    model_revision: str | None = None
    device: str | None = None
    execution_location: str = "voice-runtime"
    manifest_digest: str | None = None
    synthetic: bool = False
    audio_variant_id: str = "original"
    source_audio_digest: str | None = None
    lineage_id: str | None = None
    text: str = ""
    words: tuple[TranscriptionWord, ...] = ()
    segments: tuple[TranscriptionSegment, ...] = ()
    language: str | None = None
    duration_ms: int | None = None
    confidence: float | None = None
    latency_ms: float | None = None
    real_time_factor: float | None = None
    status: str = "succeeded"
    error: CandidateError | None = None
    warnings: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    parent_candidate_ids: tuple[str, ...] = ()

    @classmethod
    def from_result(
        cls,
        *,
        candidate_id: str,
        backend: str,
        result: "TranscriptionResult",
        audio_variant_id: str = "original",
        latency_ms: float | None = None,
        parent_candidate_ids: tuple[str, ...] = (),
        source_audio_digest: str | None = None,
        lineage_id: str | None = None,
    ) -> "TranscriptionCandidate":
        duration_seconds = (result.duration_ms or 0) / 1000.0
        words = tuple(
            replace(word, candidate_id=candidate_id)
            for segment in result.segments
            for word in segment.words
        )
        segments = tuple(
            replace(
                segment,
                candidate_id=candidate_id,
                words=tuple(replace(word, candidate_id=candidate_id) for word in segment.words),
            )
            for segment in result.segments
        )
        return cls(
            candidate_id=candidate_id,
            backend=backend,
            model=result.model,
            model_revision=str(result.provenance.get("model_revision") or "") or None,
            device=str(result.provenance.get("device") or "") or None,
            execution_location=str(result.provenance.get("execution_location") or "voice-runtime"),
            manifest_digest=str(result.provenance.get("manifest_digest") or "") or None,
            synthetic=bool(result.provenance.get("synthetic", False)),
            audio_variant_id=audio_variant_id,
            source_audio_digest=source_audio_digest,
            lineage_id=lineage_id or (parent_candidate_ids[0] if parent_candidate_ids else candidate_id),
            text=result.text,
            words=words,
            segments=segments,
            language=result.language,
            duration_ms=result.duration_ms,
            confidence=result.confidence,
            latency_ms=latency_ms,
            real_time_factor=(latency_ms / 1000.0 / duration_seconds)
            if latency_ms is not None and duration_seconds
            else None,
            status="succeeded",
            warnings=result.warnings,
            provenance={**dict(result.provenance), "raw_backend": result.raw_backend or backend},
            parent_candidate_ids=parent_candidate_ids,
        )

    @classmethod
    def failed(
        cls,
        *,
        candidate_id: str,
        backend: str,
        code: str,
        message: str,
        retriable: bool,
        audio_variant_id: str = "original",
        source_audio_digest: str | None = None,
        lineage_id: str | None = None,
    ) -> "TranscriptionCandidate":
        return cls(
            candidate_id=candidate_id,
            backend=backend,
            audio_variant_id=audio_variant_id,
            source_audio_digest=source_audio_digest,
            lineage_id=lineage_id or candidate_id,
            status="failed",
            error=CandidateError(code=code, message=message, retriable=retriable),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "backend": self.backend,
            "model": self.model,
            "model_revision": self.model_revision,
            "device": self.device,
            "execution_location": self.execution_location,
            "manifest_digest": self.manifest_digest,
            "synthetic": self.synthetic,
            "audio_variant_id": self.audio_variant_id,
            "source_audio_digest": self.source_audio_digest,
            "lineage_id": self.lineage_id,
            "text": self.text,
            "words": [word.as_dict() for word in self.words],
            "segments": [segment.as_dict() for segment in self.segments],
            "language": self.language,
            "duration_ms": self.duration_ms,
            "confidence": self.confidence,
            "latency_ms": self.latency_ms,
            "real_time_factor": self.real_time_factor,
            "status": self.status,
            "error": self.error.as_dict() if self.error else None,
            "warnings": list(self.warnings),
            "provenance": dict(self.provenance),
            "parent_candidate_ids": list(self.parent_candidate_ids),
        }


@dataclass(frozen=True)
class DisagreementRegion:
    region_id: str
    start_ms: int | None
    end_ms: int | None
    alternatives: tuple[Mapping[str, Any], ...]
    selected_candidate_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "alternatives": [dict(item) for item in self.alternatives],
            "selected_candidate_id": self.selected_candidate_id,
        }


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    duration_ms: int | None = None
    model: str | None = None
    warnings: tuple[str, ...] = ()
    segments: tuple[TranscriptionSegment, ...] = ()
    pipeline: str | None = None
    confidence: float | None = None
    raw_backend: str | None = None
    rerun_backend: str | None = None
    stages: tuple[dict, ...] = ()
    candidates: tuple[TranscriptionCandidate, ...] = ()
    selected_candidate_id: str | None = None
    fusion_strategy: str | None = None
    disagreement_regions: tuple[DisagreementRegion, ...] = ()
    decision_trace: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    provenance_valid: bool = True
    schema_version: str = "2.0"

    def with_additional_warnings(self, warnings: list[str]) -> "TranscriptionResult":
        return replace(self, warnings=tuple([*self.warnings, *warnings]))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "text": self.text,
            "language": self.language,
            "duration_ms": self.duration_ms,
            "model": self.model,
            "warnings": list(self.warnings),
            "segments": [segment.as_dict() for segment in self.segments],
            "pipeline": self.pipeline,
            "confidence": self.confidence,
            "raw_backend": self.raw_backend,
            "rerun_backend": self.rerun_backend,
            "stages": list(self.stages),
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "selected_candidate_id": self.selected_candidate_id,
            "fusion_strategy": self.fusion_strategy,
            "disagreement_regions": [region.as_dict() for region in self.disagreement_regions],
            "decision_trace": dict(self.decision_trace),
            "provenance": dict(self.provenance),
            "provenance_valid": self.provenance_valid,
        }


@dataclass(frozen=True)
class ChatResult:
    text: str
    transcript: str | None = None
    tool_intent: dict | None = None


class VoiceBackend(Protocol):
    def name(self) -> str: ...

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult: ...

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult: ...

    def list_models(self) -> list[dict]: ...

    def context_capabilities(self) -> frozenset[str]: ...


class VoiceBackendResolver(Protocol):
    """Port for resolving a policy-selected backend through runtime routing."""

    def resolve(self, backend_id: str) -> VoiceBackend: ...
