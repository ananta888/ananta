"""Backwards-compatible re-export shim.

The original monolithic test_operator_tui_shell.py (5105 LOC, 219 tests) has
been split into themed submodules under tests/test_operator_tui_shell_pkg/
(an _pkg suffix avoids pytest's file/package basename clash with this shim).

Subdomain files in tests/test_operator_tui_shell_pkg/:
  - test_tui_shell_render.py   - initial paint, sections, basic render, smoke
  - test_tui_snake.py          - snake / frame mode / fullscreen overlay
  - test_tui_visual.py         - visual viewport / split / copy / region / trail
  - test_tui_ai_chat.py        - ai_snake_config / chat input + panel
  - test_tui_tutorial_ai.py    - tutorial ai + LLM-backed hints
  - test_tui_audit.py          - audit section + cleanup confirmations
  - test_tui_templates.py      - templates section + editor
  - test_tui_nav_mouse_tab.py  - nav clicks, mouse, tab bar
  - test_tui_header.py         - header / snake header / logo
  - test_tui_command_modes.py  - command mode input, keybindings, esc/enter

All 219 tests are re-exported so the existing
`pytest tests/test_operator_tui_shell.py` collection path keeps working.
"""

from __future__ import annotations

from tests.test_operator_tui_shell_pkg.test_tui_ai_chat import *  # noqa: F401,F403
from tests.test_operator_tui_shell_pkg.test_tui_audit import *  # noqa: F401,F403
from tests.test_operator_tui_shell_pkg.test_tui_command_modes import *  # noqa: F401,F403
from tests.test_operator_tui_shell_pkg.test_tui_header import *  # noqa: F401,F403
from tests.test_operator_tui_shell_pkg.test_tui_nav_mouse_tab import *  # noqa: F401,F403
from tests.test_operator_tui_shell_pkg.test_tui_shell_render import *  # noqa: F401,F403
from tests.test_operator_tui_shell_pkg.test_tui_snake import *  # noqa: F401,F403
from tests.test_operator_tui_shell_pkg.test_tui_templates import *  # noqa: F401,F403
from tests.test_operator_tui_shell_pkg.test_tui_tutorial_ai import *  # noqa: F401,F403
from tests.test_operator_tui_shell_pkg.test_tui_visual import *  # noqa: F401,F403
