from __future__ import annotations

from unittest.mock import patch

from client_surfaces.operator_tui.keybindings_config import (
    area_keybinding_conflicts,
    ctrl_m_binding_diagnostics,
    ctrl_m_is_unsafe,
    key_for_action,
    keybinding_conflicts,
)


def test_no_global_conflicts_in_default_config():
    conflicts = keybinding_conflicts()
    assert conflicts == [], f"Unexpected conflicts: {conflicts}"


def test_no_shortcuts_area_conflicts():
    conflicts = area_keybinding_conflicts("shortcuts")
    assert conflicts == [], f"Unexpected shortcuts-area conflicts: {conflicts}"


def test_ctrl_m_not_in_default_config():
    assert ctrl_m_is_unsafe() is False


def test_ctrl_m_diagnostics_returns_none_when_not_configured():
    assert ctrl_m_binding_diagnostics() is None


def test_ctrl_4_is_default_view_switcher_key():
    key = key_for_action("toggle_visual_view_switcher_overlay", "c-4")
    assert key == "c-4"


def test_ctrl_5_is_default_next_view_key():
    key = key_for_action("next_visual_view", "c-5")
    assert key == "c-5"


def test_ctrl_6_is_default_previous_view_key():
    key = key_for_action("previous_visual_view", "c-6")
    assert key == "c-6"


def test_ctrl_3_is_default_switch_center_to_doc_view_key():
    key = key_for_action("switch_center_to_doc_view", "c-3")
    assert key == "c-3"


def test_ctrl_m_detected_as_unsafe_when_configured(tmp_path):
    import json
    kb = {
        "schema_version": "test",
        "bindings": [
            {"action": "toggle_visual_view_switcher_overlay", "key": "c-m", "display": "Ctrl+M", "label": "View-Leiste", "areas": []},
            {"action": "focus_left", "key": "f7", "display": "F7", "label": "Fokus links", "areas": []},
        ],
    }
    f = tmp_path / "kb.json"
    f.write_text(json.dumps(kb))
    with patch("client_surfaces.operator_tui.keybindings_config._resolve_config_file", return_value=f):
        from client_surfaces.operator_tui.keybindings_config import _load_bindings
        _load_bindings.cache_clear()
        try:
            assert ctrl_m_is_unsafe() is True
            diag = ctrl_m_binding_diagnostics()
            assert diag is not None
            assert "carriage-return" in str(diag["unsafe_reason"]).lower()
        finally:
            _load_bindings.cache_clear()


def test_duplicate_key_detected_as_conflict(tmp_path):
    import json
    kb = {
        "schema_version": "test",
        "bindings": [
            {"action": "action_a", "key": "f8", "display": "F8", "label": "A", "areas": ["shortcuts"]},
            {"action": "action_b", "key": "f8", "display": "F8", "label": "B", "areas": ["shortcuts"]},
        ],
    }
    f = tmp_path / "kb.json"
    f.write_text(json.dumps(kb))
    with patch("client_surfaces.operator_tui.keybindings_config._resolve_config_file", return_value=f):
        from client_surfaces.operator_tui.keybindings_config import _load_bindings
        _load_bindings.cache_clear()
        try:
            conflicts = keybinding_conflicts()
            assert any(c["key"] == "f8" for c in conflicts)
        finally:
            _load_bindings.cache_clear()
