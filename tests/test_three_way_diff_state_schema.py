from __future__ import annotations

from client_surfaces.operator_tui.diff.three_way_diff_state import build_three_way_diff_session, validate_three_way_diff_session


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

