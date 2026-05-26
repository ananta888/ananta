from __future__ import annotations

import pytest

from client_surfaces.operator_tui.diff.diff_sources import build_current_diff_source_ref
from client_surfaces.operator_tui.diff.three_way_diff_state import (
    build_three_way_diff_session,
    set_panel_state,
    validate_three_way_diff_session,
)


def test_three_way_diff_session_schema_accepts_minimal_payload() -> None:
    payload = build_three_way_diff_session(session_id="session-1")
    assert validate_three_way_diff_session(payload) == []


def test_three_way_diff_session_schema_accepts_full_payload() -> None:
    payload = build_three_way_diff_session(session_id="session-2", goal_id="goal-2", layout_mode="focus")
    payload["extensions"] = {"ai_context": {"mode": "review"}}
    assert validate_three_way_diff_session(payload) == []


def test_three_way_diff_session_schema_rejects_invalid_panel_count() -> None:
    payload = build_three_way_diff_session(session_id="session-3")
    payload["panels"] = payload["panels"][:2]
    errors = validate_three_way_diff_session(payload)
    assert any("panels" in err for err in errors)


def test_set_panel_state_updates_target_panel() -> None:
    payload = build_three_way_diff_session(session_id="session-4")
    updated = set_panel_state(
        payload,
        panel_id="B",
        panel_type="diff",
        source_left=build_current_diff_source_ref(),
        source_right=None,
        render_mode="summary",
    )
    panel_b = next(item for item in updated["panels"] if item["panel_id"] == "B")
    assert panel_b["panel_type"] == "diff"
    assert panel_b["render_mode"] == "summary"


def test_set_panel_state_rejects_unknown_panel() -> None:
    payload = build_three_way_diff_session(session_id="session-5")
    with pytest.raises(ValueError):
        set_panel_state(payload, panel_id="Z", panel_type="diff", source_left=None, source_right=None, render_mode="unified")
