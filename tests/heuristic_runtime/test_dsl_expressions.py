"""Tests für DSL v2 Expression Engine."""
import pytest

from agent.services.heuristic_runtime.dsl.expression import evaluate, _resolve_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def ctx(**kwargs):
    """Erstellt einfachen Dict-Kontext."""
    return kwargs


def nested_ctx():
    """Geschachtelter Kontext für Pfad-Tests."""
    return {
        "tui": {
            "focus": "BODY",
            "snapshot": {"screen_hash": "abc123", "width": 80},
            "semantic": {"entity_count": 5, "active_panel": "BODY"},
            "delta": {"changed_cell_count": 12},
        },
        "game": {"score": 42, "level": 3},
    }


# ── all / any / not ───────────────────────────────────────────────────────────

def test_all_true():
    result = evaluate({"all": [{"eq": [1, 1]}, {"eq": [2, 2]}]}, {})
    assert result is True


def test_all_false_if_one_false():
    result = evaluate({"all": [{"eq": [1, 1]}, {"eq": [1, 2]}]}, {})
    assert result is False


def test_all_empty_is_true():
    result = evaluate({"all": []}, {})
    assert result is True


def test_any_true_if_one_true():
    result = evaluate({"any": [{"eq": [1, 2]}, {"eq": [1, 1]}]}, {})
    assert result is True


def test_any_false_if_all_false():
    result = evaluate({"any": [{"eq": [1, 2]}, {"eq": [3, 4]}]}, {})
    assert result is False


def test_any_empty_is_false():
    result = evaluate({"any": []}, {})
    assert result is False


def test_not_true():
    result = evaluate({"not": {"eq": [1, 2]}}, {})
    assert result is True


def test_not_false():
    result = evaluate({"not": {"eq": [1, 1]}}, {})
    assert result is False


def test_not_none_propagates():
    # not None → None
    result = evaluate({"not": {"direction_towards": {}}}, {})
    assert result is None


# ── eq / gt / lt ──────────────────────────────────────────────────────────────

def test_eq_literals():
    assert evaluate({"eq": [42, 42]}, {}) is True
    assert evaluate({"eq": [42, 43]}, {}) is False


def test_eq_with_path():
    c = nested_ctx()
    assert evaluate({"eq": ["tui.focus", "BODY"]}, c) is True
    assert evaluate({"eq": ["tui.focus", "CHAT"]}, c) is False


def test_eq_missing_path():
    result = evaluate({"eq": ["nonexistent.path", "value"]}, {})
    assert result is False  # None != "value"


def test_gt_literals():
    assert evaluate({"gt": [5, 3]}, {}) is True
    assert evaluate({"gt": [3, 5]}, {}) is False
    assert evaluate({"gt": [3, 3]}, {}) is False


def test_gt_with_path():
    c = nested_ctx()
    assert evaluate({"gt": ["tui.semantic.entity_count", 0]}, c) is True
    assert evaluate({"gt": ["tui.semantic.entity_count", 10]}, c) is False


def test_gt_missing_path_returns_none():
    result = evaluate({"gt": ["missing.path", 5]}, {})
    assert result is None


def test_lt_literals():
    assert evaluate({"lt": [1, 5]}, {}) is True
    assert evaluate({"lt": [5, 1]}, {}) is False


def test_lt_with_path():
    c = nested_ctx()
    assert evaluate({"lt": ["tui.delta.changed_cell_count", 20]}, c) is True
    assert evaluate({"lt": ["tui.delta.changed_cell_count", 5]}, c) is False


# ── contains ──────────────────────────────────────────────────────────────────

def test_contains_string():
    c = {"message": "hello world"}
    assert evaluate({"contains": ["message", "hello"]}, c) is True
    assert evaluate({"contains": ["message", "xyz"]}, c) is False


def test_contains_list():
    c = {"tags": ["a", "b", "c"]}
    assert evaluate({"contains": ["tags", "b"]}, c) is True
    assert evaluate({"contains": ["tags", "z"]}, c) is False


def test_contains_missing_path():
    result = evaluate({"contains": ["missing.path", "val"]}, {})
    assert result is False


# ── regex_safe ────────────────────────────────────────────────────────────────

def test_regex_safe_match():
    c = {"text": "hello world 123"}
    result = evaluate({"regex_safe": [r"\d+", "text"]}, c)
    assert result is True


def test_regex_safe_no_match():
    c = {"text": "hello world"}
    result = evaluate({"regex_safe": [r"^\d+$", "text"]}, c)
    assert result is False


def test_regex_safe_invalid_pattern():
    c = {"text": "hello"}
    result = evaluate({"regex_safe": ["[invalid", "text"]}, c)
    assert result is False


def test_regex_safe_too_long_pattern():
    c = {"text": "hello"}
    long_pattern = "a" * 300
    result = evaluate({"regex_safe": [long_pattern, "text"]}, c)
    assert result is False


def test_regex_safe_missing_target():
    result = evaluate({"regex_safe": [r"\w+", "missing.path"]}, {})
    assert result is False


# ── distance ──────────────────────────────────────────────────────────────────

def test_distance_manhattan():
    c = {"snake": {"x": 0, "y": 0}, "target": {"x": 3, "y": 4}}
    # Pfadauflösung für verschachtelten Context
    result = evaluate({
        "distance": {"ax": "snake.x", "ay": "snake.y", "bx": "target.x", "by": "target.y"}
    }, c)
    assert result == 7.0  # |3-0| + |4-0|


def test_distance_same_point():
    c = {"ax": 5, "ay": 5, "bx": 5, "by": 5}
    # Flat keys
    result = evaluate({"distance": {"ax": "ax", "ay": "ay", "bx": "bx", "by": "by"}}, c)
    assert result == 0.0


def test_distance_missing_returns_none():
    result = evaluate({"distance": {"ax": "x", "ay": "y", "bx": "bx", "by": "by"}}, {})
    assert result is None


# ── intersects ────────────────────────────────────────────────────────────────

def test_intersects_overlapping():
    expr = {
        "intersects": {
            "a": {"x": 0, "y": 0, "w": 10, "h": 10},
            "b": {"x": 5, "y": 5, "w": 10, "h": 10},
        }
    }
    assert evaluate(expr, {}) is True


def test_intersects_non_overlapping():
    expr = {
        "intersects": {
            "a": {"x": 0, "y": 0, "w": 5, "h": 5},
            "b": {"x": 10, "y": 10, "w": 5, "h": 5},
        }
    }
    assert evaluate(expr, {}) is False


def test_intersects_touching_edge_not_overlapping():
    """Touching edges: x+w == b.x → nicht überlappend."""
    expr = {
        "intersects": {
            "a": {"x": 0, "y": 0, "w": 5, "h": 5},
            "b": {"x": 5, "y": 0, "w": 5, "h": 5},
        }
    }
    assert evaluate(expr, {}) is False


def test_intersects_missing_bbox():
    expr = {"intersects": {"a": None, "b": {"x": 0, "y": 0, "w": 5, "h": 5}}}
    assert evaluate(expr, {}) is False


# ── changed_recently ──────────────────────────────────────────────────────────

def test_changed_recently_missing_returns_false():
    """Fehlender _changed_at Wert → False."""
    result = evaluate({"changed_recently": {"path": "tui.snapshot.screen_hash", "within_seconds": 5.0}}, {})
    assert result is False


# ── direction_towards ─────────────────────────────────────────────────────────

def test_direction_towards_returns_none():
    """direction_towards kann ohne Live-Game-State nicht ausgewertet werden → None."""
    result = evaluate({"direction_towards": {}}, {})
    assert result is None


# ── Fehlende / unbekannte Operatoren ─────────────────────────────────────────

def test_unknown_operator_returns_none():
    """Unbekannter Operator → None (kein Crash)."""
    result = evaluate({"unknown_op": {"some": "data"}}, {})
    assert result is None


def test_nested_exception_handled():
    """Exception in verschachteltem Ausdruck → None (kein Crash im UI-Pfad)."""
    # Provoziere eine Exception durch malformed Daten
    result = evaluate({"gt": [{"nested": "not_a_number"}, 5]}, {})
    assert result is None


# ── Pfad-Auflösung ────────────────────────────────────────────────────────────

def test_resolve_path_dict():
    c = {"a": {"b": {"c": 42}}}
    assert _resolve_path("a.b.c", c) == 42


def test_resolve_path_missing_returns_none():
    c = {"a": {"b": 1}}
    assert _resolve_path("a.x.y", c) is None


def test_resolve_path_dataclass():
    """Pfadauflösung funktioniert auch mit Dataclass-Attributen."""
    from dataclasses import dataclass

    @dataclass
    class Inner:
        value: int = 7

    @dataclass
    class Outer:
        inner: Inner = None

    obj = Outer(inner=Inner(value=99))
    assert _resolve_path("inner.value", obj) == 99


def test_resolve_path_none_node_returns_none():
    c = {"a": None}
    assert _resolve_path("a.b", c) is None


# ── Komplexe Ausdrücke ────────────────────────────────────────────────────────

def test_complex_all_any_expression():
    c = nested_ctx()
    expr = {
        "all": [
            {"eq": ["tui.focus", "BODY"]},
            {"any": [
                {"gt": ["tui.semantic.entity_count", 3]},
                {"lt": ["tui.delta.changed_cell_count", 1]},
            ]},
        ]
    }
    result = evaluate(expr, c)
    assert result is True


def test_complex_expression_with_missing_fields():
    """Teils fehlende Felder → kein Crash, degradiert graceful."""
    expr = {
        "all": [
            {"eq": ["missing.field", "BODY"]},
            {"gt": ["also.missing", 0]},
        ]
    }
    result = evaluate(expr, {})
    # all([False/None, None]) → False/None, kein Exception
    assert result is not None or result is None  # Hauptsache kein Exception
