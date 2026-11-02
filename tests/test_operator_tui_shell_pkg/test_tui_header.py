from __future__ import annotations

import re

from client_surfaces.operator_tui.models import FocusPane, OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell


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
