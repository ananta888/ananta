from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .diff_sources import build_current_diff_source_ref, build_diff_panel_config

_SCHEMA_FILE = Path(__file__).resolve().parents[3] / "schemas" / "tui" / "three_way_diff_session.v1.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))


def validate_three_way_diff_session(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


def build_three_way_diff_session(
    *,
    session_id: str,
    goal_id: str | None = None,
    layout_mode: str = "equal",
) -> dict[str, Any]:
    now = _now_iso()
    panel_template = {
        "panel_type": "empty",
        "source_left": None,
        "source_right": None,
        "render_mode": "unified",
        "filters": {},
        "scroll_state": {"line": 0},
        "selection_state": {},
    }
    payload: dict[str, Any] = {
        "schema": "three_way_diff_session.v1",
        "session_id": str(session_id),
        "panels": [
            {"panel_id": "A", **panel_template},
            {"panel_id": "B", **panel_template},
            {"panel_id": "C", **panel_template},
        ],
        "active_panel": "A",
        "layout_mode": str(layout_mode),
        "created_at": now,
        "updated_at": now,
        "extensions": {},
    }
    if goal_id:
        payload["goal_id"] = str(goal_id)
    errors = validate_three_way_diff_session(payload)
    if errors:
        raise ValueError(f"invalid_three_way_diff_session:{'; '.join(errors)}")
    return payload


def set_panel_state(
    session: dict[str, Any],
    *,
    panel_id: str,
    panel_type: str,
    source_left: dict[str, Any] | None,
    source_right: dict[str, Any] | None,
    render_mode: str,
) -> dict[str, Any]:
    payload = dict(session)
    panels = [dict(item) for item in list(payload.get("panels") or [])]
    target = next((item for item in panels if str(item.get("panel_id") or "") == panel_id), None)
    if target is None:
        raise ValueError("unknown_panel_id")
    build_diff_panel_config(panel_id=panel_id, render_mode=render_mode, filters=target.get("filters") or {})
    target["panel_type"] = panel_type
    target["source_left"] = dict(source_left) if isinstance(source_left, dict) else source_left
    target["source_right"] = dict(source_right) if isinstance(source_right, dict) else source_right
    target["render_mode"] = render_mode
    payload["panels"] = panels
    payload["updated_at"] = _now_iso()
    errors = validate_three_way_diff_session(payload)
    if errors:
        raise ValueError(f"invalid_three_way_diff_session:{'; '.join(errors)}")
    return payload


def build_current_diff_three_panel_session(*, session_id: str, goal_id: str | None = None) -> dict[str, Any]:
    session = build_three_way_diff_session(session_id=session_id, goal_id=goal_id)
    current = build_current_diff_source_ref()
    session = set_panel_state(
        session,
        panel_id="A",
        panel_type="diff",
        source_left=current,
        source_right=None,
        render_mode="unified",
    )
    session = set_panel_state(
        session,
        panel_id="B",
        panel_type="diff",
        source_left=current,
        source_right=None,
        render_mode="summary",
    )
    session = set_panel_state(
        session,
        panel_id="C",
        panel_type="ai_review",
        source_left=None,
        source_right=None,
        render_mode="ai_review",
    )
    return session
