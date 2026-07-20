from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from voice_runtime.backends.base import TranscriptionResult
from voice_runtime.fusion.consensus import _result_hash
from voice_runtime.schemas import transcription_result_from_dict


@pytest.mark.parametrize(
    ("authority", "correction_state"),
    [
        ("provisional", "pending"),
        ("final", "not_requested"),
        ("corrected", "completed"),
        ("correction_failed", "failed"),
        ("missing_source", "missing_source"),
    ],
)
def test_additive_revision_result_roundtrips(authority: str, correction_state: str) -> None:
    raw = {
        "text": "Hallo Welt",
        "turn_id": "turn-1",
        "revision": 2,
        "authority": authority,
        "source_digest": "a" * 64,
        "semantic_frame_refs": ["frame:1"],
        "correction_state": correction_state,
        "supersedes_revision": 1,
        "future_safe_extension": {"version": 1},
    }
    result = transcription_result_from_dict(raw)
    assert result.as_dict()["future_safe_extension"] == {"version": 1}
    assert result.turn_id == "turn-1" and result.revision == 2


def test_legacy_result_remains_readable_and_security_unknowns_fail_closed() -> None:
    assert transcription_result_from_dict({"text": "legacy"}).text == "legacy"
    with pytest.raises(ValueError, match="security-sensitive"):
        transcription_result_from_dict({"text": "bad", "private_key": "secret"})


def test_result_parser_enforces_text_reference_and_array_budgets() -> None:
    with pytest.raises(ValueError, match="string exceeds"):
        transcription_result_from_dict({"text": "x" * 65_537})
    with pytest.raises(ValueError, match="array exceeds"):
        transcription_result_from_dict({"text": "x", "semantic_frame_refs": ["f"] * 257})


def test_additive_default_fields_preserve_the_legacy_canonical_hash_projection() -> None:
    legacy_projection = {
        "schema_version": "2.0",
        "text": "legacy",
        "language": None,
        "duration_ms": None,
        "model": None,
        "warnings": [],
        "segments": [],
        "pipeline": None,
        "confidence": None,
        "raw_backend": None,
        "rerun_backend": None,
        "stages": [],
        "candidates": [],
        "selected_candidate_id": None,
        "fusion_strategy": None,
        "disagreement_regions": [],
        "decision_trace": {},
        "provenance": {},
        "provenance_valid": True,
    }
    result = TranscriptionResult(
        text="legacy",
        turn_id=None,
        revision=None,
        authority=None,
        source_digest=None,
        semantic_frame_refs=(),
        correction_state=None,
        supersedes_revision=None,
        extensions={},
    )
    expected_hash = hashlib.sha256(
        json.dumps(
            legacy_projection,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    assert result.as_dict() == legacy_projection
    assert _result_hash(result) == expected_hash


def test_set_additive_fields_remain_serialized_and_hash_relevant() -> None:
    legacy = TranscriptionResult(text="legacy")
    variants = (
        replace(legacy, turn_id="turn-1"),
        replace(legacy, revision=2),
        replace(legacy, authority="corrected"),
        replace(legacy, source_digest="a" * 64),
        replace(legacy, semantic_frame_refs=("frame:1",)),
        replace(legacy, correction_state="completed"),
        replace(legacy, supersedes_revision=1),
        replace(legacy, extensions={"future_safe_extension": {"version": 1}}),
    )
    hashes = {_result_hash(legacy), *(_result_hash(item) for item in variants)}

    assert len(hashes) == len(variants) + 1
    assert variants[0].as_dict()["turn_id"] == "turn-1"
    assert variants[4].as_dict()["semantic_frame_refs"] == ["frame:1"]
    assert variants[-1].as_dict()["future_safe_extension"] == {"version": 1}
