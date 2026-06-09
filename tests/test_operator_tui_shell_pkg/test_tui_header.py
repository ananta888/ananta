from __future__ import annotations

import json
import os
import re
import sys
import time
from importlib import reload
from argparse import Namespace
from pathlib import Path

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.app import build_initial_state, load_active_section
from client_surfaces.operator_tui.actions import dispatch_action, parse_action
from client_surfaces.operator_tui.ai_snake_config_view import (
    ai_snake_config_items,
    apply_ai_snake_config_value,
    chat_model_option_label,
    refresh_chat_backend_models,
)
from client_surfaces.operator_tui.browser import browser_fallback_url
from client_surfaces.operator_tui.capabilities import graphics_decision
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.diagrams import detect_diagram_blocks, render_diagram_fallback
from client_surfaces.operator_tui.markdown_renderer import render_markdown_lines
from client_surfaces.operator_tui.interactive import InteractiveOperatorTui
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState, PanelState, SectionLoadResult
from client_surfaces.operator_tui.performance import measure
from client_surfaces.operator_tui.read_models import build_goal_rows, build_task_rows
from client_surfaces.operator_tui.refresh import refresh_policy_for, should_refresh
from client_surfaces.operator_tui.renderer import _overlay_fullscreen_snake, render_operator_shell
from client_surfaces.operator_tui.rollout import operator_tui_enabled, rollback_hint, rollout_stage
from client_surfaces.operator_tui.sections import SECTIONS, move_section, normalize_section_id
from client_surfaces.operator_tui.smoke import run_fixture_smoke
from client_surfaces.operator_tui.snake_persistence import load_tui_chat_settings, save_tui_chat_settings
from client_surfaces.operator_tui.chat_state import sanitize_text
from agent.cli.main import _run_tui


def test_header_focus_hints_show_snake_controls() -> None:
    game = {
        "active": True,
        "alive": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(4, 3), (3, 3), (2, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (8, 3),
        "score": 2,
        "moves": 5,
        "last_move": 0.0,
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.HEADER,
        header_logo_game=game,
    )

    output = render_operator_shell(state, width=140, height=24)

    assert "Snake-Modus aktiv  running" in output
    assert "[SNAKE]" in output
    assert "Ctrl+X=Markieren" in output
    assert "[Ctrl+S] Snake" in output
    assert "AI-Config" in output
    assert ":config" in output



def test_header_lists_snakes_with_oidc_pseudonym_color_and_message() -> None:
    game = {
        "active": True,
        "alive": True,
        "score": 1,
        "local_snake_id": "s1",
        "snakes": {
            "s1": {
                "id": "s1",
                "pseudonym": "alice",
                "oidc_provider": "keycloak",
                "snake_color": "mint",
                "message": "hello",
            },
            "s2": {
                "id": "s2",
                "pseudonym": "bob",
                "oidc_provider": "entra",
                "snake_color": "violet",
                "message": "world",
            },
        },
        "snake": [(3, 2), (2, 2)],
        "trail_path": [(3, 2), (2, 2)],
        "free_mode": True,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)

    output = render_operator_shell(state, width=120, height=28)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)

    assert "alice@keycloak [mint]: hello" in plain
    assert "bob@entra [violet]: world" in plain
    assert "Mode    =" not in plain
    assert "\x1b[38;2;170;255;210mS1 alice@keycloak [mint]\x1b[0m" in output
    assert "\x1b[38;2;96;215;165mhello\x1b[0m" in output



def test_non_snake_mode_uses_logo_header_instead_of_snake_panel() -> None:
    game = {
        "active": False,
        "ui_steering": False,
        "alive": True,
        "local_snake_id": "s1",
        "snakes": {
            "s1": {"id": "s1", "pseudonym": "alice", "snake_color": "mint"},
            "s-ai": {"id": "s-ai", "pseudonym": "tutor-ai", "snake_color": "amber"},
        },
        "snake": [(4, 2), (3, 2)],
        "trail_path": [(4, 2), (3, 2)],
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION, header_logo_game=game)

    output = render_operator_shell(state, width=120, height=28)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    header = "\n".join(plain.splitlines()[:8])

    assert "Ctrl+S startet Snake-Modus" not in header
    assert "S1 alice [mint] access=full" not in header
    assert "S-AI tutor-ai [amber] access=view" not in header



def test_inactive_header_shows_logo_and_hides_snake_explanation() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION, header_logo_game={})
    output = render_operator_shell(state, width=120, height=28)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    header = "\n".join(plain.splitlines()[:8])

    assert "Ctrl+S startet Snake-Modus" not in header
    assert "Freigaben: :snake-access" not in header


