from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from client_surfaces.operator_tui.ai_snake_training_data import (
    BEHAVIOR_EVENT_SCHEMA_FILE,
    LEARNED_PATTERN_SCHEMA_FILE,
    PROFILE_SCHEMA_FILE,
    TRAINING_BUNDLE_SCHEMA_FILE,
    load_schema,
    profile_examples,
    validate_payload,
)


def _validator(schema_file: str) -> Draft202012Validator:
    schema = load_schema(schema_file)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_schemas_exist_and_are_draft_2020_12_compatible() -> None:
    for name in (
        PROFILE_SCHEMA_FILE,
        BEHAVIOR_EVENT_SCHEMA_FILE,
        LEARNED_PATTERN_SCHEMA_FILE,
        TRAINING_BUNDLE_SCHEMA_FILE,
    ):
        path = Path("schemas/tui") / name
        assert path.exists(), f"missing schema: {name}"
        _validator(name)


def test_profile_schema_validates_empty_normal_and_extended_examples() -> None:
    validator = _validator(PROFILE_SCHEMA_FILE)
    for sample in profile_examples():
        assert list(validator.iter_errors(sample)) == []


def test_profile_schema_rejects_unknown_top_level_field() -> None:
    sample = profile_examples()[0]
    sample["unexpected"] = "nope"
    errors = validate_payload(sample, schema_filename=PROFILE_SCHEMA_FILE)
    assert errors


def test_behavior_event_schema_blocks_private_raw_notes_and_too_large_payload() -> None:
    validator = _validator(BEHAVIOR_EVENT_SCHEMA_FILE)
    valid = {
        "event_id": "ev-1",
        "created_at": "2026-05-26T10:00:00Z",
        "event_type": "section_change",
        "source": "tui",
        "normalized_value": "section=artifacts",
        "refs": {"section_ref": "artifacts"},
        "privacy_class": "workspace",
        "retention_hint": "standard",
        "human_label": "Artifacts geöffnet",
    }
    assert list(validator.iter_errors(valid)) == []
    invalid_private = dict(valid)
    invalid_private["privacy_class"] = "private_local"
    invalid_private["normalized_value"] = "Hier ist mein geheimer Notizinhalt"
    assert list(validator.iter_errors(invalid_private))
    invalid_size = dict(valid)
    invalid_size["normalized_value"] = "x" * 400
    assert list(validator.iter_errors(invalid_size))


def test_learned_pattern_schema_validates_transparent_rule() -> None:
    validator = _validator(LEARNED_PATTERN_SCHEMA_FILE)
    payload = {
        "pattern_id": "p-artifact-explain-001",
        "pattern_type": "sequence_rule",
        "conditions": {
            "all": [
                {"field": "section_is", "op": "eq", "value": "artifacts"},
                {"field": "channel_is", "op": "eq", "value": "ai:tutor"},
            ]
        },
        "predicted_intent": "artifact_explain",
        "confidence": 0.72,
        "evidence": {"source_event_ids": ["ev-1", "ev-2", "ev-3"], "sample_size": 3},
        "counters": {"hits": 5, "misses": 1, "positives": 4, "negatives": 1},
        "last_seen_at": "2026-05-26T10:00:00Z",
        "expires_at": "2026-06-26T10:00:00Z",
        "status": "draft",
        "human_explanation": "Wenn Artifacts und AI-Chat aktiv sind, wird oft Erklärung gewünscht.",
        "ai_hint": "artifact_explain when section=artifacts + channel=ai:tutor",
    }
    assert list(validator.iter_errors(payload)) == []


def test_training_bundle_schema_allows_bundle_without_events_sample() -> None:
    validator = _validator(TRAINING_BUNDLE_SCHEMA_FILE)
    bundle = {
        "bundle_id": "b-1",
        "schema_version": "ai_snake_training_bundle.v1",
        "exported_at": "2026-05-26T10:00:00Z",
        "source": {"app": "ananta-tui", "version": "1.0"},
        "profile": profile_examples()[1],
        "patterns": [],
        "checksums": {
            "profile_sha256": "a" * 64,
            "patterns_sha256": "b" * 64,
        },
        "human_readme": "Bundle für manuellen Transfer.",
        "ai_readme": "Use profile and patterns as compact training context.",
        "privacy_manifest": {
            "public_ui": 1,
            "workspace": 1,
            "private_local": 0,
            "sensitive_blocked": 0,
        },
        "extensions": {"origin": "test"},
    }
    assert list(validator.iter_errors(bundle)) == []
    # ensure json-serializable stable output
    json.dumps(bundle, ensure_ascii=False)
