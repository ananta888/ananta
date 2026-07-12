from __future__ import annotations

import pytest

from voice_runtime.backends.base import CandidateError, TranscriptionCandidate, TranscriptionResult
from voice_runtime.schemas import transcription_result_from_dict, transcription_result_json_schema


def test_legacy_transcription_payload_remains_readable():
    result = transcription_result_from_dict(
        {
            "text": "Hallo",
            "language": "de",
            "duration_ms": 100,
            "model": "legacy",
            "warnings": [],
        }
    )

    assert result.text == "Hallo"
    assert result.schema_version == "1.0"
    assert result.candidates == ()


def test_new_transcription_result_roundtrips_with_typed_candidate_error():
    original = TranscriptionResult(
        text="Hallo",
        candidates=(
            TranscriptionCandidate(
                candidate_id="candidate-failed",
                backend="vosk",
                status="failed",
                error=CandidateError("timeout", "candidate timed out", True),
                source_audio_digest="sha256:audio",
                lineage_id="candidate-failed",
            ),
        ),
        provenance_valid=False,
    )

    parsed = transcription_result_from_dict(original.as_dict())

    assert parsed.as_dict() == original.as_dict()
    assert parsed.candidates[0].error.code == "timeout"
    assert transcription_result_json_schema()["$id"] == "ananta.voice-transcription-result.v2"


def test_failed_candidate_without_typed_error_is_rejected():
    with pytest.raises(ValueError, match="typed error"):
        transcription_result_from_dict(
            {
                "text": "",
                "candidates": [{"candidate_id": "candidate-1", "backend": "vosk", "status": "failed"}],
            }
        )
