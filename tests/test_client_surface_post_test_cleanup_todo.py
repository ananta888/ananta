from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEANUP_PATH = ROOT / "data" / "client_surface_post_test_cleanup_todo.json"


def _load_payload() -> dict:
    return json.loads(CLEANUP_PATH.read_text(encoding="utf-8"))


def test_post_test_cleanup_todo_contains_categorized_remaining_gaps() -> None:
    payload = _load_payload()
    items = list(payload.get("items") or [])
    categories = {str(item.get("category")) for item in items}

    assert payload.get("schema") == "client_surface_post_test_cleanup_todo_v1"
    assert "missing_implementation" in categories
    assert "weak_test_coverage" in categories
    assert "documentation_drift" in categories
    assert "future_enhancement" in categories


def test_post_test_cleanup_todo_excludes_unrelated_scopes() -> None:
    payload = _load_payload()
    exclusions = {str(item).lower() for item in list(payload.get("excluded_scopes") or [])}
    descriptions = " ".join(str(item.get("description") or "") for item in list(payload.get("items") or [])).lower()

    assert "kritis" in exclusions
    assert "ref-profile" in exclusions
    assert "kritis" not in descriptions
    assert "ref-profile" not in descriptions
