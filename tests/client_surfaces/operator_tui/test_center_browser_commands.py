from __future__ import annotations

import unittest

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
        self.assertEqual(key_for_action("center_browser_toggle", "f5"), "f5")
        self.assertEqual(area_keybinding_conflicts("footer-normal"), [])


if __name__ == "__main__":
    unittest.main()
