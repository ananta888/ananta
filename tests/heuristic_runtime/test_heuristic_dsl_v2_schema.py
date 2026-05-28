"""Tests für heuristic_dsl.v2 JSON-Schema — Validierung von DSL-Beispielen."""
import json
import pathlib
import pytest

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

SCHEMA_DIR = pathlib.Path(__file__).parent.parent.parent / "schemas" / "heuristic"
DSL_SCHEMA_PATH = SCHEMA_DIR / "heuristic_dsl.v2.json"


@pytest.fixture
def dsl_schema():
    with open(DSL_SCHEMA_PATH) as f:
        return json.load(f)


def validate_dsl(dsl_doc: dict, schema: dict) -> None:
    """Validiert ein DSL-Dokument gegen das Schema."""
    jsonschema.validate(instance=dsl_doc, schema=schema)


def _minimal_dsl(**overrides) -> dict:
    """Minimales gültiges DSL-Dokument."""
    base = {
        "dsl_version": "2.0",
        "observe": {"sources": ["tui.snapshot"]},
        "action": {"kind": "no_action"},
        "safety": {"safety_class": "ui_motion_only"},
        "provenance": {"created_by": "test", "rationale": "test rationale"},
    }
    base.update(overrides)
    return base


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_minimal_valid_dsl(dsl_schema):
    """Minimales DSL-Dokument ist gültig."""
    doc = _minimal_dsl()
    validate_dsl(doc, dsl_schema)


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_missing_provenance_rejected(dsl_schema):
    """DSL ohne provenance wird abgelehnt."""
    doc = {
        "dsl_version": "2.0",
        "observe": {"sources": ["tui.snapshot"]},
        "action": {"kind": "no_action"},
        "safety": {"safety_class": "ui_motion_only"},
        # provenance fehlt
    }
    with pytest.raises(jsonschema.ValidationError):
        validate_dsl(doc, dsl_schema)


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_missing_action_rejected(dsl_schema):
    """DSL ohne action wird abgelehnt."""
    doc = {
        "dsl_version": "2.0",
        "observe": {"sources": ["tui.snapshot"]},
        "safety": {"safety_class": "ui_motion_only"},
        "provenance": {"created_by": "test", "rationale": "test"},
        # action fehlt
    }
    with pytest.raises(jsonschema.ValidationError):
        validate_dsl(doc, dsl_schema)


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_invalid_dsl_version_rejected(dsl_schema):
    """Ungültige dsl_version wird abgelehnt."""
    doc = _minimal_dsl()
    doc["dsl_version"] = "1.0"  # nur 2.0 erlaubt
    with pytest.raises(jsonschema.ValidationError):
        validate_dsl(doc, dsl_schema)


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_invalid_action_kind_rejected(dsl_schema):
    """Ungültige action.kind wird abgelehnt."""
    doc = _minimal_dsl()
    doc["action"] = {"kind": "inline_code"}  # verboten
    with pytest.raises(jsonschema.ValidationError):
        validate_dsl(doc, dsl_schema)


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_invalid_safety_class_rejected(dsl_schema):
    """Ungültige safety_class wird abgelehnt."""
    doc = _minimal_dsl()
    doc["safety"] = {"safety_class": "elevated"}  # nicht erlaubt in DSL v2
    with pytest.raises(jsonschema.ValidationError):
        validate_dsl(doc, dsl_schema)


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_invalid_observe_source_rejected(dsl_schema):
    """Ungültige observe source wird abgelehnt."""
    doc = _minimal_dsl()
    doc["observe"] = {"sources": ["unknown.source"]}
    with pytest.raises(jsonschema.ValidationError):
        validate_dsl(doc, dsl_schema)


# ── Drei Beispiel-Heuristiken ─────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_example_mouse_follow_artifact(dsl_schema):
    """Beispiel: mouse_follow_artifact DSL ist gültig."""
    doc = {
        "dsl_version": "2.0",
        "observe": {
            "sources": ["tui.semantic", "tui.mouse"],
        },
        "match": {
            "all": [
                {"eq": ["tui.focus", "BODY"]},
                {"gt": ["tui.semantic.entity_count", 0]},
            ]
        },
        "action": {
            "kind": "follow_artifact",
            "confidence": 0.7,
            "max_step": 2,
        },
        "lease": {
            "ttl_seconds": 5.0,
            "refresh_on": ["mouse_direction_changed"],
        },
        "safety": {"safety_class": "ui_motion_only"},
        "provenance": {
            "created_by": "example",
            "rationale": "Folgt dem Artifact nahe dem Mauszeiger",
        },
    }
    validate_dsl(doc, dsl_schema)


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_example_idle_lurk(dsl_schema):
    """Beispiel: idle_lurk DSL ist gültig."""
    doc = {
        "dsl_version": "2.0",
        "observe": {
            "sources": ["tui.snapshot", "tui.delta"],
            "window": {"last_n_snapshots": 5, "last_n_deltas": 20},
        },
        "match": {
            "all": [
                {"eq": ["tui.focus", "BODY"]},
                {"lt": ["tui.delta.changed_cell_count", 5]},
            ]
        },
        "action": {
            "kind": "lurk_near",
            "confidence": 0.6,
            "min_distance": 3,
        },
        "lease": {
            "ttl_seconds": 8.0,
            "refresh_on": ["screen_hash_changed"],
        },
        "safety": {"safety_class": "ui_motion_only"},
        "provenance": {
            "created_by": "example",
            "rationale": "Ruhig verweilen bei stabilem Screen",
        },
    }
    validate_dsl(doc, dsl_schema)


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_example_changed_region_attention(dsl_schema):
    """Beispiel: changed_region_attention DSL ist gültig."""
    doc = {
        "dsl_version": "2.0",
        "observe": {
            "sources": ["tui.snapshot", "tui.delta", "tui.semantic"],
            "window": {"last_n_snapshots": 3, "last_n_deltas": 10},
        },
        "match": {
            "all": [
                {"eq": ["tui.focus", "BODY"]},
                {"gt": ["tui.delta.changed_cell_count", 10]},
                {"changed_recently": {"path": "tui.snapshot.screen_hash", "within_seconds": 3.0}},
            ]
        },
        "score": {"base": 0.75},
        "action": {
            "kind": "smooth_follow",
            "confidence": 0.75,
            "max_step": 3,
            "acceleration_limit": 1.5,
        },
        "lease": {
            "ttl_seconds": 4.0,
            "refresh_on": ["screen_hash_changed", "semantic_target_changed"],
        },
        "safety": {
            "safety_class": "ui_motion_only",
            "allowed_capabilities": ["read_local_context"],
        },
        "provenance": {
            "created_by": "example",
            "rationale": "Aktive UI-Änderungen → sanft folgen",
            "model": "test-model",
            "created_at": "2026-05-28T00:00:00Z",
        },
    }
    validate_dsl(doc, dsl_schema)


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_match_expression_all_any(dsl_schema):
    """match-Ausdruck mit all/any ist gültig."""
    doc = _minimal_dsl()
    doc["match"] = {
        "any": [
            {"eq": ["tui.focus", "BODY"]},
            {"all": [
                {"gt": ["tui.semantic.entity_count", 0]},
                {"lt": ["tui.semantic.entity_count", 100]},
            ]},
        ]
    }
    validate_dsl(doc, dsl_schema)
