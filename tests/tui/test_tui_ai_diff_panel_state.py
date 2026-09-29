from __future__ import annotations

import pytest

from client_surfaces.operator_tui.diff.ai_diff_panel_state import (
    build_ai_diff_panel_state,
    set_ai_diff_mode,
    validate_ai_diff_panel_state,
)


def test_ai_diff_panel_state_contains_required_fields() -> None:
    payload = build_ai_diff_panel_state(mode="review", selected_panels=["A", "B"], selected_hunks=["h1"], context_refs=["ctx:1"])
    assert payload["mode"] == "review"
    assert payload["selected_panels"] == ["A", "B"]
    assert payload["selected_hunks"] == ["h1"]
    assert payload["context_refs"] == ["ctx:1"]
    assert payload["prompt_template_ref"] == "prompt:diff3/review"
    assert validate_ai_diff_panel_state(payload) == []


def test_ai_diff_panel_state_allows_supported_modes() -> None:
    for mode in ["review", "explain", "risk", "tests", "patch", "chat"]:
        payload = build_ai_diff_panel_state(mode=mode, selected_panels=["A"])
        assert payload["mode"] == mode
        assert validate_ai_diff_panel_state(payload) == []


def test_ai_diff_panel_state_mode_switch_updates_prompt_template() -> None:
    payload = build_ai_diff_panel_state(mode="review", selected_panels=["A", "B"])
    switched = set_ai_diff_mode(payload, mode="patch", status="running")
    assert switched["mode"] == "patch"
    assert switched["prompt_template_ref"] == "prompt:diff3/patch"
    assert switched["status"] == "running"


def test_ai_diff_panel_state_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError):
        build_ai_diff_panel_state(mode="invalid", selected_panels=["A"])

