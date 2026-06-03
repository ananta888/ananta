from __future__ import annotations

import unittest
from unittest.mock import patch
from types import SimpleNamespace

from client_surfaces.operator_tui.commands import execute_center_browser_command
from client_surfaces.operator_tui.keybindings_config import area_keybinding_conflicts, key_for_action
from client_surfaces.operator_tui.models import OperatorState


class TestCenterBrowserCommands(unittest.TestCase):
    def test_toggle_sets_browser_requested(self):
        state = OperatorState(endpoint="http://hub")
        result = execute_center_browser_command("center.browser.toggle", state)
        assert result is not None
        game = result.state.header_logo_game or {}
        self.assertTrue(game.get("center_browser_active"))
        self.assertEqual(game.get("center_browser_status"), "requested")

    def test_exit_clears_browser_active(self):
        state = OperatorState(endpoint="http://hub", header_logo_game={"center_browser_active": True})
        result = execute_center_browser_command("center.browser.exit", state)
        assert result is not None
        game = result.state.header_logo_game or {}
        self.assertFalse(game.get("center_browser_active"))
        self.assertEqual(game.get("center_browser_status"), "exited")

    def test_default_browser_keybinding_has_no_footer_conflict(self):
        # Default keybinding for center_browser_toggle is c-b (set in default.json)
        self.assertEqual(key_for_action("center_browser_toggle", "c-2"), "c-b")
        self.assertEqual(area_keybinding_conflicts("footer-normal"), [])

    def test_center_webview_open_sets_data_url_requested(self):
        state = OperatorState(endpoint="http://hub")
        with patch(
            "client_surfaces.operator_tui.visual.runtime.capability_detector.detect_carbonyl_browser",
            return_value=SimpleNamespace(available=True, unavailable_reason=""),
        ):
            result = execute_center_browser_command("center.webview.open", state)
        assert result is not None
        game = result.state.header_logo_game or {}
        self.assertTrue(game.get("center_browser_active"))
        self.assertEqual(game.get("center_browser_status"), "requested")
        self.assertTrue(str(game.get("center_browser_url") or "").startswith("data:text/html"))

    def test_center_window_open_sets_window_command(self):
        state = OperatorState(endpoint="http://hub")
        result = execute_center_browser_command("center.window.open", state)
        assert result is not None
        game = result.state.header_logo_game or {}
        self.assertEqual(game.get("center_window_command"), "center.window.open")

    def test_center_window_status_sets_window_command(self):
        state = OperatorState(endpoint="http://hub")
        result = execute_center_browser_command("center.window.status", state)
        assert result is not None
        game = result.state.header_logo_game or {}
        self.assertEqual(game.get("center_window_command"), "center.window.status")

    def test_center_window_view_sets_mode_request(self):
        state = OperatorState(endpoint="http://hub")
        result = execute_center_browser_command("center.window.view doc", state)
        assert result is not None
        game = result.state.header_logo_game or {}
        self.assertEqual(game.get("center_window_view_mode_request"), "doc")


if __name__ == "__main__":
    unittest.main()
