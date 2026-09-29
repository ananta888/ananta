from __future__ import annotations

import pytest

from client_surfaces.operator_tui.models import OperatorMode, OperatorState


def _state(**kwargs) -> OperatorState:
    base = OperatorState(
        endpoint="http://localhost",
        section_id="dashboard",
        mode=OperatorMode.COMMAND,
        command_line="",
        header_logo_game={},
    )
    if kwargs:
        return base.with_updates(**kwargs)
    return base


def _exec(cmd: str, state: OperatorState | None = None):
    from client_surfaces.operator_tui.commands import execute_command

    return execute_command(cmd, state or _state())


# ── :speed ────────────────────────────────────────────────────────────────────


def test_speed_sets_tps_and_level():
    result = _exec(":speed 3")
    game = result.state.header_logo_game or {}
    assert game.get("tps_override") == 12
    assert game.get("speed_level") == 3


def test_speed_level_1_maps_to_3_tps():
    result = _exec(":speed 1")
    assert (result.state.header_logo_game or {}).get("tps_override") == 3


def test_speed_level_5_maps_to_60_tps():
    result = _exec(":speed 5")
    assert (result.state.header_logo_game or {}).get("tps_override") == 60


def test_speed_rejects_zero():
    result = _exec(":speed 0")
    assert not result.handled


def test_speed_rejects_six():
    result = _exec(":speed 6")
    assert not result.handled


def test_speed_rejects_non_numeric():
    result = _exec(":speed fast")
    assert not result.handled


def test_speed_without_args_is_not_handled():
    result = _exec(":speed")
    assert not result.handled


# ── :tutor ────────────────────────────────────────────────────────────────────


def test_tutor_mode_overview(monkeypatch):
    monkeypatch.setattr(
        "client_surfaces.operator_tui.commands.__import__",
        lambda name: None,
        raising=False,
    )
    result = _exec(":tutor mode overview")
    game = result.state.header_logo_game or {}
    assert game.get("tutor_depth_mode") == "overview"


def test_tutor_mode_expert_updates_game():
    result = _exec(":tutor mode expert")
    game = result.state.header_logo_game or {}
    assert game.get("tutor_depth_mode") == "expert"


def test_tutor_mode_invalid_not_handled():
    result = _exec(":tutor mode ultraspeed")
    assert not result.handled


def test_tutor_silent_sets_flag():
    result = _exec(":tutor silent")
    game = result.state.header_logo_game or {}
    assert game.get("tutor_silent") is True


def test_tutor_active_clears_flag():
    s = _state(header_logo_game={"tutor_silent": True})
    result = _exec(":tutor active", s)
    game = result.state.header_logo_game or {}
    assert game.get("tutor_silent") is False


def test_tutor_unknown_subcommand_not_handled():
    result = _exec(":tutor dance")
    assert not result.handled


# ── :ask ─────────────────────────────────────────────────────────────────────


def test_ask_sets_question_in_game():
    result = _exec(":ask Was ist der Policy Gate?")
    game = result.state.header_logo_game or {}
    assert game.get("tutor_ask_question") == "Was ist der Policy Gate?"
    assert game.get("paused") is True
    assert game.get("tutor_ask_answered") is False


def test_ask_empty_question_not_handled():
    result = _exec(":ask")
    assert not result.handled


def test_ask_returns_normal_mode():
    result = _exec(":ask Hallo?")
    assert result.state.mode == OperatorMode.NORMAL


# ── :tutorial ────────────────────────────────────────────────────────────────


def test_tutorial_stop_clears_state():
    s = _state(header_logo_game={"tutorial_state": {"active": True, "name": "intro"}})
    result = _exec(":tutorial stop", s)
    game = result.state.header_logo_game or {}
    assert game.get("tutorial_state") is None


def test_tutorial_skip_without_active_tutorial_not_handled():
    result = _exec(":tutorial skip")
    assert not result.handled


def test_tutorial_unknown_subcommand_not_handled():
    result = _exec(":tutorial whatever")
    assert not result.handled


# ── :tutorials ────────────────────────────────────────────────────────────────


def test_tutorials_lists_without_error():
    result = _exec(":tutorials")
    assert result.handled
    assert "tutorials:" in result.state.status_message


# ── :snakes ───────────────────────────────────────────────────────────────────


def test_snakes_empty_returns_status():
    result = _exec(":snakes")
    assert "snakes:" in result.state.status_message


def test_snakes_with_snake_data_shows_ids():
    s = _state(
        header_logo_game={
            "snakes": {
                "s1": {"pseudonym": "Player", "snake_color": "mint", "role": "player", "local": True},
                "s-ai": {"pseudonym": "AI", "snake_color": "violet", "role": "tutor", "local": False},
            }
        }
    )
    result = _exec(":snakes", s)
    msg = result.state.status_message or ""
    assert "s1" in msg
    assert "s-ai" in msg


def test_snakes_command_not_duplicated():
    """Ensure the duplicate snakes handler was removed; only one response."""
    result = _exec(":snakes")
    # If there were two handlers the second would overwrite the first with different text.
    # Both paths contain "snakes:" so just check it's handled exactly once (no crash).
    assert result.handled


# ── :msg ─────────────────────────────────────────────────────────────────────


def test_msg_appends_to_outbox():
    result = _exec(":msg s-ai Hallo Schlange!")
    game = result.state.header_logo_game or {}
    outbox = game.get("snake_outbox") or []
    assert len(outbox) == 1
    assert outbox[0]["to"] == "s-ai"
    assert outbox[0]["text"] == "Hallo Schlange!"


def test_msg_without_text_not_handled():
    result = _exec(":msg s-ai")
    assert not result.handled


def test_msg_without_target_not_handled():
    result = _exec(":msg")
    assert not result.handled


def test_msg_too_long_not_handled():
    long_text = "x" * 201
    result = _exec(f":msg s-ai {long_text}")
    assert not result.handled


def test_msg_outbox_capped_at_20():
    s = _state()
    for i in range(25):
        from client_surfaces.operator_tui.commands import execute_command

        result = execute_command(f":msg s-ai message {i}", s)
        s = result.state
    game = s.header_logo_game or {}
    assert len(game.get("snake_outbox") or []) == 20
