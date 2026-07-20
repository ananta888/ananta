from __future__ import annotations

import json
import math
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = tuple(sorted((ROOT / "schemas/webrtc").glob("speech_*.v1.json")))


def _base(version: str) -> dict:
    return {
        "version": version,
        "session_id": "session-a",
        "epoch": 1,
        "sender_id": "alice",
        "audience_id": "bob",
        "consent_version": 1,
        "expires_at_ms": 10_000,
        "contract_digest": "a" * 64,
    }


def _valid(path: Path) -> dict:
    name = path.name
    if name.startswith("speech_capability"):
        return {
            **_base("ananta.speech-capability.v1"),
            "modes": ["transcript_live"],
            "live_partials": True,
            "segment_duration_ms": 30000,
            "max_frame_bytes": 16384,
        }
    if name.startswith("speech_transport"):
        return {
            **_base("ananta.speech-transport.v1"),
            "mode": "transcript_live",
            "live_partials": True,
            "correct_each_segment": True,
            "traffic_classes": ["control", "transcript"],
            "source_ttl_ms": 60000,
        }
    contextual = {**_base(""), "turn_id": "turn-a", "revision": 1, "source_digest": "b" * 64}
    if name.startswith("speech_semantic"):
        return {
            **contextual,
            "version": "ananta.speech-semantic-frame.v1",
            "frame_id": "frame-a",
            "start_ms": 0,
            "end_ms": 20,
            "algorithm_version": "prosody-v1",
            "confidence": 0.8,
            "prosody": [0.1],
            "residual": [],
        }
    if name.startswith("speech_correction"):
        return {
            **contextual,
            "version": "ananta.speech-correction.v1",
            "supersedes_revision": 1,
            "revision": 2,
            "authority": "corrected",
            "text": "Hallo",
            "operations": [],
            "reason_code": "corrected",
        }
    return {
        **contextual,
        "version": "ananta.speech-quality-report.v1",
        "source_digest": None,
        "loss_ratio": 0.0,
        "queue_bytes": 0,
        "partial_age_ms": 10,
        "correction_lag_ms": 20,
        "source_loss_ratio": 0.0,
        "feature_loss_ratio": 0.0,
        "reconstruction_error_ratio": 0.0,
        "recommended_mode": "transcript_live",
        "reason_code": "healthy",
    }


@pytest.mark.parametrize("path", SCHEMAS, ids=lambda path: path.name)
def test_speech_schemas_are_closed_bounded_and_reject_unknown_or_nonfinite(path: Path) -> None:
    schema = json.loads(path.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    valid = _valid(path)
    validator.validate(valid)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**valid, "local_path": "/secret/audio.wav"})
    numeric = dict(valid)
    field = next((name for name in ("confidence", "loss_ratio") if name in numeric), None)
    if field:
        numeric[field] = math.nan
        # JSON itself has no NaN literal. The canonical wire encoder uses
        # allow_nan=False before schema dispatch, so non-finite values cannot
        # cross the runtime boundary even though Python's jsonschema accepts
        # its non-standard in-memory float extension.
        with pytest.raises(ValueError):
            json.dumps(numeric, allow_nan=False)


def test_semantic_frame_rejects_oversize_vectors_and_raw_key_fields() -> None:
    path = ROOT / "schemas/webrtc/speech_semantic_frame.v1.json"
    validator = jsonschema.Draft202012Validator(json.loads(path.read_text()))
    valid = _valid(path)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**valid, "prosody": [0.0] * 33})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**valid, "raw_key": "forbidden"})
