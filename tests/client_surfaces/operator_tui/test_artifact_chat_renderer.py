from __future__ import annotations

from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell


def test_artifact_chat_header_shows_active_artifact() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="artifacts",
        header_logo_game={
            "tutorial_mode": True,
            "artifact_chat_state": {"active_target": {"label": "operator_tui_splash.cast"}},
            "mouse_follow_enabled": True,
            "mouse_state": {"active": True, "x": 12, "y": 10},
            "artifact_intent_confidence": "confirmed",
            "tutorial_ai_target_mode": "fast_target",
            "tutorial_propose_history": [],
        },
    )
    output = render_operator_shell(state, width=120, height=32)
    assert "context: operator_tui_splash.cast" in output


def test_ai_response_is_rendered_near_target() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="artifacts",
        header_logo_game={
            "tutorial_mode": True,
            "tutorial_propose_history": [
                {"source": "openai-compatible", "target": "content", "text": "Artifacts enthalten Cast und E2E Reports."}
            ],
        },
    )
    output = render_operator_shell(state, width=120, height=32)
    assert "Artifacts enthalten Cast" in output


def test_backend_error_keeps_context() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="artifacts",
        header_logo_game={
            "tutorial_mode": True,
            "artifact_chat_state": {
                "active_target": {"label": "operator_tui_splash.cast"},
                "error": "backend timeout",
            },
        },
    )
    output = render_operator_shell(state, width=120, height=32)
    assert "operator_tui_splash.cast" in output
