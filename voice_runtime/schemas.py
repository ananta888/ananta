from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .backends.base import (
    CandidateError,
    DisagreementRegion,
    TranscriptionCandidate,
    TranscriptionResult,
    TranscriptionSegment,
    TranscriptionWord,
)


@dataclass(frozen=True)
class ApiError:
    code: str
    message: str
    retriable: bool = False
    status: int = 400
    request_id: str | None = None

    def to_response(self) -> tuple[dict, int]:
        return (
            {
                "error": {
                    "code": self.code,
                    "message": self.message,
                    "retriable": self.retriable,
                    "request_id": self.request_id,
                }
            },
            self.status,
        )


def transcription_result_from_dict(raw: Mapping[str, Any]) -> TranscriptionResult:
    if not isinstance(raw, Mapping):
        raise ValueError("transcription result must be an object")
    _validate_result_budgets(raw)
    candidates = tuple(_candidate(item) for item in _objects(raw.get("candidates")))
    candidate_ids = [item.candidate_id for item in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("transcription result contains duplicate candidate IDs")
    return TranscriptionResult(
        schema_version=str(raw.get("schema_version") or "1.0"),
        text=str(raw.get("text") or ""),
        language=_optional_string(raw.get("language")),
        duration_ms=_optional_int(raw.get("duration_ms")),
        model=_optional_string(raw.get("model")),
        warnings=tuple(str(item) for item in (raw.get("warnings") or [])),
        segments=tuple(_segment(item) for item in _objects(raw.get("segments"))),
        pipeline=_optional_string(raw.get("pipeline")),
        confidence=_optional_float(raw.get("confidence")),
        raw_backend=_optional_string(raw.get("raw_backend")),
        rerun_backend=_optional_string(raw.get("rerun_backend")),
        stages=tuple(dict(item) for item in _objects(raw.get("stages"))),
        candidates=candidates,
        selected_candidate_id=_optional_string(raw.get("selected_candidate_id")),
        fusion_strategy=_optional_string(raw.get("fusion_strategy")),
        disagreement_regions=tuple(_disagreement(item) for item in _objects(raw.get("disagreement_regions"))),
        decision_trace=dict(raw.get("decision_trace") or {}),
        provenance=dict(raw.get("provenance") or {}),
        provenance_valid=bool(raw.get("provenance_valid", True)),
        turn_id=_optional_bounded_string(raw.get("turn_id"), 128),
        revision=_optional_bounded_integer(raw.get("revision"), 1, 2**31 - 1),
        authority=_optional_choice(
            raw.get("authority"),
            {"provisional", "final", "corrected", "correction_failed", "missing_source"},
        ),
        source_digest=_optional_digest(raw.get("source_digest")),
        semantic_frame_refs=tuple(
            _bounded_string(item, 256) for item in _bounded_array(raw.get("semantic_frame_refs"), 256)
        ),
        correction_state=_optional_choice(
            raw.get("correction_state"),
            {"not_requested", "pending", "completed", "failed", "missing_source"},
        ),
        supersedes_revision=_optional_bounded_integer(raw.get("supersedes_revision"), 1, 2**31 - 1),
        extensions=_result_extensions(raw),
    )


def transcription_result_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "ananta.voice-transcription-result.v2",
        "type": "object",
        "required": ["text"],
        "properties": {
            "schema_version": {"type": "string"},
            "text": {"type": "string"},
            "language": {"type": ["string", "null"]},
            "duration_ms": {"type": ["integer", "null"], "minimum": 0},
            "model": {"type": ["string", "null"]},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "segments": {"type": "array", "items": {"type": "object"}},
            "candidates": {"type": "array", "items": {"$ref": "#/$defs/candidate"}},
            "selected_candidate_id": {"type": ["string", "null"]},
            "fusion_strategy": {"type": ["string", "null"]},
            "disagreement_regions": {"type": "array", "items": {"type": "object"}},
            "decision_trace": {"type": "object"},
            "provenance": {"type": "object"},
            "provenance_valid": {"type": "boolean"},
            "turn_id": {"type": ["string", "null"], "maxLength": 128},
            "revision": {"type": ["integer", "null"], "minimum": 1},
            "authority": {"enum": ["provisional", "final", "corrected", "correction_failed", "missing_source", None]},
            "source_digest": {"type": ["string", "null"], "pattern": "^[a-f0-9]{64}$"},
            "semantic_frame_refs": {"type": "array", "maxItems": 256, "items": {"type": "string", "maxLength": 256}},
            "correction_state": {"enum": ["not_requested", "pending", "completed", "failed", "missing_source", None]},
            "supersedes_revision": {"type": ["integer", "null"], "minimum": 1},
        },
        "additionalProperties": True,
        "$defs": {
            "candidate": {
                "type": "object",
                "required": ["candidate_id", "backend", "status"],
                "properties": {
                    "candidate_id": {"type": "string", "minLength": 1},
                    "backend": {"type": "string", "minLength": 1},
                    "status": {"enum": ["succeeded", "failed", "skipped"]},
                    "error": {
                        "anyOf": [
                            {"type": "null"},
                            {
                                "type": "object",
                                "required": ["code", "message", "retriable"],
                            },
                        ]
                    },
                },
                "additionalProperties": True,
            }
        },
    }


def _candidate(raw: Mapping[str, Any]) -> TranscriptionCandidate:
    candidate_id = str(raw.get("candidate_id") or "").strip()
    backend = str(raw.get("backend") or "").strip()
    status = str(raw.get("status") or "succeeded")
    if not candidate_id or not backend or status not in {"succeeded", "failed", "skipped"}:
        raise ValueError("candidate identity or status is invalid")
    error_raw = raw.get("error")
    error = None
    if isinstance(error_raw, Mapping):
        error = CandidateError(
            code=str(error_raw.get("code") or "unknown"),
            message=str(error_raw.get("message") or "candidate failed"),
            retriable=bool(error_raw.get("retriable", False)),
        )
    if status == "failed" and error is None:
        raise ValueError("failed candidate requires a typed error")
    return TranscriptionCandidate(
        candidate_id=candidate_id,
        backend=backend,
        model=_optional_string(raw.get("model")),
        model_revision=_optional_string(raw.get("model_revision")),
        device=_optional_string(raw.get("device")),
        execution_location=str(raw.get("execution_location") or "voice-runtime"),
        manifest_digest=_optional_string(raw.get("manifest_digest")),
        synthetic=bool(raw.get("synthetic", False)),
        audio_variant_id=str(raw.get("audio_variant_id") or "original"),
        source_audio_digest=_optional_string(raw.get("source_audio_digest")),
        lineage_id=_optional_string(raw.get("lineage_id")),
        text=str(raw.get("text") or ""),
        words=tuple(_word(item) for item in _objects(raw.get("words"))),
        segments=tuple(_segment(item) for item in _objects(raw.get("segments"))),
        language=_optional_string(raw.get("language")),
        duration_ms=_optional_int(raw.get("duration_ms")),
        confidence=_optional_float(raw.get("confidence")),
        latency_ms=_optional_float(raw.get("latency_ms")),
        real_time_factor=_optional_float(raw.get("real_time_factor")),
        status=status,
        error=error,
        warnings=tuple(str(item) for item in (raw.get("warnings") or [])),
        provenance=dict(raw.get("provenance") or {}),
        parent_candidate_ids=tuple(str(item) for item in (raw.get("parent_candidate_ids") or [])),
    )


def _segment(raw: Mapping[str, Any]) -> TranscriptionSegment:
    return TranscriptionSegment(
        start_ms=int(raw.get("start_ms") or 0),
        end_ms=int(raw.get("end_ms") or 0),
        text=str(raw.get("text") or ""),
        confidence=_optional_float(raw.get("confidence")),
        speaker=_optional_string(raw.get("speaker")),
        backend=_optional_string(raw.get("backend")),
        warnings=tuple(str(item) for item in (raw.get("warnings") or [])),
        candidate_id=_optional_string(raw.get("candidate_id")),
        words=tuple(_word(item) for item in _objects(raw.get("words"))),
    )


def _word(raw: Mapping[str, Any]) -> TranscriptionWord:
    return TranscriptionWord(
        start_ms=int(raw.get("start_ms") or 0),
        end_ms=int(raw.get("end_ms") or 0),
        text=str(raw.get("text") or ""),
        confidence=_optional_float(raw.get("confidence")),
        candidate_id=_optional_string(raw.get("candidate_id")),
    )


def _disagreement(raw: Mapping[str, Any]) -> DisagreementRegion:
    return DisagreementRegion(
        region_id=str(raw.get("region_id") or ""),
        start_ms=_optional_int(raw.get("start_ms")),
        end_ms=_optional_int(raw.get("end_ms")),
        alternatives=tuple(dict(item) for item in _objects(raw.get("alternatives"))),
        selected_candidate_id=_optional_string(raw.get("selected_candidate_id")),
    )


def _objects(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError("contract array must contain objects")
    return tuple(value)


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, (str, int, float, bytes, bytearray)):
        raise ValueError("contract integer must be numeric")
    return int(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (str, int, float, bytes, bytearray)):
        raise ValueError("contract number must be numeric")
    return float(value)


_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "text",
        "language",
        "duration_ms",
        "model",
        "warnings",
        "segments",
        "pipeline",
        "confidence",
        "raw_backend",
        "rerun_backend",
        "stages",
        "candidates",
        "selected_candidate_id",
        "fusion_strategy",
        "disagreement_regions",
        "decision_trace",
        "provenance",
        "provenance_valid",
        "turn_id",
        "revision",
        "authority",
        "source_digest",
        "semantic_frame_refs",
        "correction_state",
        "supersedes_revision",
    }
)
_SECURITY_FIELD_PARTS = ("private_key", "raw_key", "secret", "local_path", "nonce")


def _validate_result_budgets(raw: Mapping[str, Any]) -> None:
    _bounded_string(raw.get("text") or "", 65_536)
    for name, maximum in (
        ("warnings", 128),
        ("segments", 4096),
        ("stages", 128),
        ("candidates", 32),
        ("disagreement_regions", 2048),
        ("semantic_frame_refs", 256),
    ):
        _bounded_array(raw.get(name), maximum)
    for name in set(raw) - _RESULT_KEYS:
        if any(part in str(name).casefold() for part in _SECURITY_FIELD_PARTS):
            raise ValueError(f"unknown security-sensitive transcription field: {name}")
    if len(raw) > 128:
        raise ValueError("transcription result contains too many fields")


def _result_extensions(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in raw.items() if key not in _RESULT_KEYS}


def _bounded_array(value: object, maximum: int) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ValueError("contract array exceeds its budget")
    return tuple(value)


def _bounded_string(value: object, maximum: int) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise ValueError("contract string exceeds its budget")
    return value


def _optional_bounded_string(value: object, maximum: int) -> str | None:
    return None if value is None else _bounded_string(value, maximum)


def _optional_bounded_integer(value: object, low: int, high: int) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError("contract integer is outside its budget")
    return value


def _optional_choice(value: object, choices: set[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in choices:
        raise ValueError("contract enum value is invalid")
    return value


def _optional_digest(value: object) -> str | None:
    if value is None:
        return None
    candidate = _bounded_string(value, 64)
    if len(candidate) != 64 or any(character not in "0123456789abcdef" for character in candidate):
        raise ValueError("contract digest is invalid")
    return candidate
