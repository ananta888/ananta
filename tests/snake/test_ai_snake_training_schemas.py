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


def test_schema_fixtures_validate() -> None:
    base = Path("tests/fixtures/ai_snake_training")
    profile = json.loads((base / "profile.valid.json").read_text(encoding="utf-8"))
    event = json.loads((base / "event.valid.json").read_text(encoding="utf-8"))
    pattern = json.loads((base / "pattern.valid.json").read_text(encoding="utf-8"))
    bundle = json.loads((base / "bundle.valid.json").read_text(encoding="utf-8"))

    assert validate_payload(profile, schema_filename=PROFILE_SCHEMA_FILE) == []
    assert validate_payload(event, schema_filename=BEHAVIOR_EVENT_SCHEMA_FILE) == []
    assert validate_payload(pattern, schema_filename=LEARNED_PATTERN_SCHEMA_FILE) == []
    assert validate_payload(bundle, schema_filename=TRAINING_BUNDLE_SCHEMA_FILE) == []


def test_negative_schema_cases_missing_required_and_large_text() -> None:
    missing_required = profile_examples()[0]
    missing_required.pop("display_name", None)
    errs = validate_payload(missing_required, schema_filename=PROFILE_SCHEMA_FILE)
    assert errs

    unknown_field_bundle = {
        "bundle_id": "b-unknown",
        "schema_version": "ai_snake_training_bundle.v1",
        "exported_at": "2026-05-26T10:00:00Z",
        "source": {"app": "ananta-tui", "version": "1.0"},
        "profile": profile_examples()[0],
        "patterns": [],
        "checksums": {"profile_sha256": "a" * 64, "patterns_sha256": "b" * 64},
        "human_readme": "ok",
        "ai_readme": "ok",
        "privacy_manifest": {"public_ui": 0, "workspace": 0, "private_local": 0, "sensitive_blocked": 0},
        "unknown_top_level": True,
    }
    assert validate_payload(unknown_field_bundle, schema_filename=TRAINING_BUNDLE_SCHEMA_FILE)

    too_large = {
        "pattern_id": "p-overflow",
        "pattern_type": "heuristic",
        "conditions": {"field": "section_is", "op": "eq", "value": "dashboard"},
        "predicted_intent": "navigate",
        "confidence": 0.2,
        "evidence": {"source_event_ids": [], "sample_size": 0},
        "counters": {"hits": 0, "misses": 0, "positives": 0, "negatives": 0},
        "last_seen_at": "2026-05-26T10:00:00Z",
        "expires_at": "2026-05-27T10:00:00Z",
        "status": "draft",
        "human_explanation": "x" * 700,
        "ai_hint": "y" * 400,
    }
    assert validate_payload(too_large, schema_filename=LEARNED_PATTERN_SCHEMA_FILE)
