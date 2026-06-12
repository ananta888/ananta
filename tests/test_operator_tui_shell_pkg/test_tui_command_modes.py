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


def test_operator_tui_focus_command_is_typed() -> None:
    state = OperatorState(endpoint="http://localhost:5000")

    result = execute_command(":focus detail", state)

    assert result.handled is True
    assert result.state.focus is FocusPane.DETAIL



def test_operator_tui_mouse_command_toggles_follow_mode() -> None:
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={"mouse_follow_enabled": False})

    on = execute_command(":mouse on", state)
    toggle = execute_command(":mouse", on.state)
    off = execute_command(":mouse off", toggle.state)

    assert on.handled is True
    assert bool((on.state.header_logo_game or {}).get("mouse_follow_enabled")) is True
    assert bool((toggle.state.header_logo_game or {}).get("mouse_follow_enabled")) is False
    assert bool((off.state.header_logo_game or {}).get("mouse_follow_enabled")) is False



def test_operator_tui_ai_command_controls_mode_and_status() -> None:
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={"ai_snake_mode": "lurking_follow"})

    follow = execute_command(":ai follow", state)
    status = execute_command(":ai status", follow.state)
    explain = execute_command(":ai explain", status.state)

    assert follow.handled is True
    assert (follow.state.header_logo_game or {}).get("ai_snake_mode") == "follow"
    assert "ai:follow" in status.state.status_message
    assert (explain.state.header_logo_game or {}).get("ai_force_question") is True



def test_ananta_tui_default_uses_operator_render_once(capsys) -> None:
    exit_code = _run_tui(["--render-once", "--skip-splash", "--section", "tasks", "--width", "90", "--height", "20"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "ananta" in captured.out



def test_ananta_tui_help_lists_logo_renderer_flags(capsys) -> None:
    exit_code = _run_tui(["--help"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "--logo-renderer" in captured.out
    assert "--logo-animation" in captured.out
    assert "--logo-fps" in captured.out
    assert "--no-logo" in captured.out



def test_operator_tui_inspect_and_browser_commands_render_context() -> None:
    state = load_active_section(OperatorState(endpoint="http://localhost:5000", section_id="tasks"))
    state = execute_command(":inspect", state).state
    state = execute_command(":browser TUI-T26", state).state
    output = render_operator_shell(state, width=110, height=48)

    assert "inspect:" in output
    assert "browser=http://localhost:5000/t" in output



def test_codecompass_outputs_auto_build_from_repo_when_missing(tmp_path, monkeypatch) -> None:
    script = tmp_path / "codecompass_rag.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "out = None",
                "for i, part in enumerate(sys.argv):",
                "    if part == '-o' and i + 1 < len(sys.argv):",
                "        out = sys.argv[i + 1]",
                "if not out:",
                "    raise SystemExit(2)",
                "import pathlib",
                "out_dir = pathlib.Path(out)",
                "out_dir.mkdir(parents=True, exist_ok=True)",
                "(out_dir / 'index.jsonl').write_text(json.dumps({'kind':'function_symbol','file':'client_surfaces/operator_tui/interactive.py','name':'_tutorial_ai_tip'}) + '\\n', encoding='utf-8')",
                "raise SystemExit(0)",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANANTA_TUI_AUTO_BUILD_CODECOMPASS", "1")
    state = OperatorState(endpoint="http://localhost:5000", section_id="dashboard", focus=FocusPane.CONTENT)
    tui = InteractiveOperatorTui(state)

    resolved = tui._resolve_codecompass_output_dir()
    if resolved is None:
        for _ in range(40):
            time.sleep(0.05)
            resolved = tui._resolve_codecompass_output_dir()
            if resolved is not None:
                break

    assert resolved is not None
    assert (resolved / "index.jsonl").exists()



def test_local_selection_uses_local_snake_color() -> None:
    lines = ["abcdefghij"]
    game = {
        "active": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake_color": "mint",
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0)],
        "selection_cells": [(2, 0)],
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)
    out = _overlay_fullscreen_snake(lines, state, width=10)

    assert "\x1b[48;2;170;255;210m" in out[0]



def test_free_mode_snake_board_keeps_full_terminal_width(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "snake": [(118, 4), (117, 4), (116, 4)],
        "trail_path": [(118, 4), (117, 4), (116, 4)],
        "mark_cells": [],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "vel_x": 0.0,
        "vel_y": 0.0,
        "accum_x": 0.0,
        "accum_y": 0.0,
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr("client_surfaces.operator_tui.snake_tick_mixin.time.monotonic", lambda: 1.0)
    monkeypatch.setattr("client_surfaces.operator_tui.snake_tick_mixin.shutil.get_terminal_size", lambda fallback: os.terminal_size((120, 32)))

    tui._tick_header_snake()

    updated = tui.state.header_logo_game or {}
    assert updated.get("board_w") == 120



def test_command_backspace_updates_command_line() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        mode=OperatorMode.COMMAND,
        command_line="abcd",
    )
    tui = InteractiveOperatorTui(state)
    tui._command_buffer = "abcd"

    tui._command_backspace()

    assert tui.state.command_line == "abc"
    assert tui._command_buffer == "abc"



def test_command_backspace_on_empty_exits_command_mode() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        mode=OperatorMode.COMMAND,
        command_line="",
        header_logo_game={},
    )
    tui = InteractiveOperatorTui(state)
    tui._command_buffer = ""
    tui._command_cursor = 0

    tui._command_backspace()

    assert tui.state.mode is OperatorMode.NORMAL
    assert tui.state.command_line == ""
    assert "command: beendet" in str(tui.state.status_message or "")



def test_command_input_supports_cursor_delete_and_history() -> None:
    state = OperatorState(endpoint="http://localhost:5000", mode=OperatorMode.COMMAND, command_line="")
    tui = InteractiveOperatorTui(state)
    tui._command_buffer = "abcde"
    tui._command_cursor = 5
    tui._sync_command_line_state()

    tui._command_move_cursor(-2)
    tui._command_backspace()
    tui._command_delete()

    assert tui.state.command_line == "abe"
    game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
    assert int(game.get("command_input_cursor") or 0) == 2

    tui._command_buffer = "draft"
    tui._command_cursor = 5
    tui._command_history = ["eins", "zwei"]
    tui._command_history_index = None
    tui._command_saved_draft = ""
    tui._sync_command_line_state()

    tui._command_history_move(-1)
    assert tui.state.command_line == "zwei"
    tui._command_history_move(-1)
    assert tui.state.command_line == "eins"
    tui._command_history_move(1)
    assert tui.state.command_line == "zwei"
    tui._command_history_move(1)
    assert tui.state.command_line == "draft"



def test_command_line_renders_cursor_marker() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        mode=OperatorMode.COMMAND,
        command_line="abcd",
        header_logo_game={"command_input_cursor": 2},
    )

    output = render_operator_shell(state, width=96, height=20)

    assert ":ab_cd" in output



def test_handle_quit_key_exits_in_command_mode() -> None:
    class _FakeApp:
        def __init__(self) -> None:
            self.exited = False

        def exit(self) -> None:
            self.exited = True

    class _FakeEvent:
        def __init__(self, app) -> None:
            self.app = app

    state = OperatorState(
        endpoint="http://localhost:5000",
        mode=OperatorMode.COMMAND,
        command_line=":chat backend status",
    )
    tui = InteractiveOperatorTui(state)
    tui._command_buffer = ":chat backend status"
    app = _FakeApp()

    tui._handle_quit_key(_FakeEvent(app))

    assert app.exited is True
    assert tui._command_buffer == ":chat backend status"



def test_ctrl_c_cancel_does_not_exit_chat_input_mode() -> None:
    from client_surfaces.operator_tui.chat_state import get_chat_state

    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game={"active": True})
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()
    tui._chat_append("abc")

    handled = tui._cancel_active_input_mode()

    chat = get_chat_state(dict(tui.state.header_logo_game or {}))
    assert handled is False
    assert bool(chat.get("chat_focus")) is True
    assert str(chat.get("chat_input_buffer") or "") == "abc"



def test_poll_tutor_ask_result_sets_timeout_answer() -> None:
    from concurrent.futures import Future

    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game={})
    tui = InteractiveOperatorTui(state)
    pending: Future[str] = Future()
    tui._tutor_ask_future = pending
    game = {
        "tutor_ask_question": "frage",
        "tutor_ask_answered": False,
        "_ask_submitted": True,
        "tutor_ask_at": time.monotonic() - 20.0,
        "tutor_ask_timeout_s": 5.0,
        "tutor_ask_deadline_at": time.monotonic() - 1.0,
    }

    tui._poll_tutor_ask_result(game)

    assert game.get("tutor_ask_answered") is True
    assert "Timeout" in str(game.get("tutor_ask_answer") or "")
    assert game.get("_ask_submitted") is False
    assert tui._tutor_ask_future is None



def test_command_from_chat_does_not_force_chat_focus_after_run() -> None:
    game = {"active": True, "alive": True, "ui_steering": True, "free_mode": True}
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()

    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state

    game2 = dict(tui.state.header_logo_game or {})
    chat = get_chat_state(game2)
    chat["chat_focus"] = False
    set_chat_state(game2, chat)
    tui.state = tui.state.with_updates(header_logo_game=game2, mode=OperatorMode.COMMAND, command_line="/cancel")
    tui._command_buffer = "/cancel"

    tui._run_command("/cancel")

    updated = tui.state.header_logo_game or {}
    chat_after = get_chat_state(updated)
    assert chat_after.get("chat_focus") is False
    assert tui.state.mode is OperatorMode.NORMAL



def test_toggle_snake_mode_preserves_tutorial_setting() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "tutorial_mode": False,
        "snake": [(2, 3), (1, 3), (0, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "board_w": 40,
        "board_h": 12,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._toggle_snake_mode()
    off_state = tui.state.header_logo_game or {}
    assert off_state.get("tutorial_mode") is False
    assert off_state.get("ui_steering") is False

    tui._toggle_snake_mode()
    on_state = tui.state.header_logo_game or {}
    assert on_state.get("tutorial_mode") is False
    assert on_state.get("ui_steering") is True



def test_toggle_tutorial_ai_mode_removes_visual_ai_snake_immediately() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": False,
        "free_mode": False,
        "tutorial_mode": True,
        "snakes": {
            "s1": {"id": "s1", "pseudonym": "local", "snake": [(1, 1)]},
            "s-ai": {"id": "s-ai", "pseudonym": "tutor-ai", "snake": [(2, 2)]},
        },
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._toggle_tutorial_ai_mode()

    updated = tui.state.header_logo_game or {}
    snakes = dict(updated.get("snakes") or {})
    assert updated.get("tutorial_mode") is False
    assert "s-ai" not in snakes
    assert "visual ai-snake: aus" in str(tui.state.status_message or "")



def test_config_command_toggles_ai_snake_config_panel() -> None:
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={"chat_panel_open": True})
    opened = execute_command(":config", state).state
    game_open = dict(opened.header_logo_game or {})
    assert bool(game_open.get("ai_snake_config_open")) is True
    assert opened.focus is FocusPane.CONTENT
    assert opened.mode is OperatorMode.NORMAL

    closed = execute_command(":config", opened).state
    game_closed = dict(closed.header_logo_game or {})
    assert bool(game_closed.get("ai_snake_config_open")) is False



def test_execute_command_strips_multiple_prefix_chars() -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    result = execute_command("::config", state)
    game = dict(result.state.header_logo_game or {})
    assert bool(game.get("ai_snake_config_open")) is True
    assert result.handled is True



def test_default_header_snake_loads_persisted_tui_chat_settings(tmp_path, monkeypatch) -> None:
    import client_surfaces.operator_tui.config.user_config_manager as ucm
    import client_surfaces.operator_tui.snake_persistence as sp

    monkeypatch.setattr(sp, "_config_dir", lambda: tmp_path / "ananta")
    monkeypatch.setattr(ucm, "load_user_config", lambda: {})
    save_tui_chat_settings({"chat_backend": "lmstudio", "chat_context_chars": 5000}, cwd=Path.cwd())
    tui = InteractiveOperatorTui(OperatorState(endpoint="http://localhost:5000"))

    game = tui._default_header_snake()

    assert game.get("chat_backend") == "lmstudio"
    assert game.get("chat_context_chars") == 5000



def test_default_header_snake_explicit_chat_env_overrides_persisted_settings(tmp_path, monkeypatch) -> None:
    import client_surfaces.operator_tui.config.user_config_manager as ucm
    import client_surfaces.operator_tui.snake_persistence as sp

    monkeypatch.setattr(sp, "_config_dir", lambda: tmp_path / "ananta")
    monkeypatch.setattr(ucm, "load_user_config", lambda: {"chat_backend": "lmstudio"})
    monkeypatch.setenv("ANANTA_TUI_CHAT_BACKEND", "ananta-worker")
    monkeypatch.setenv("ANANTA_TUI_CHAT_MODEL", "microsoft_-_phi-3.5-mini")
    monkeypatch.setenv("ANANTA_TUI_CHAT_RAG_TOP_K", "16")
    save_tui_chat_settings({"chat_backend": "lmstudio", "chat_rag_top_k": 48}, cwd=Path.cwd())
    tui = InteractiveOperatorTui(OperatorState(endpoint="http://localhost:5000"))

    game = tui._default_header_snake()

    assert game.get("chat_backend") == "ananta-worker"
    assert game.get("chat_backend_model") == "microsoft_-_phi-3.5-mini"
    assert game.get("chat_rag_top_k") == "16"



def test_default_header_snake_loads_persisted_chat_input_history(monkeypatch) -> None:
    import client_surfaces.operator_tui.config.user_config_manager as ucm
    import client_surfaces.operator_tui.header_snake_mixin as hsm
    from client_surfaces.operator_tui.chat_state import get_chat_state

    monkeypatch.setattr(hsm, "load_tui_chat_settings", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        ucm,
        "load_user_config",
        lambda: {
            "input_history_chat_enabled": True,
            "input_history_max_entries": 100,
            "chat_input_history": ["alte frage", "neuere frage"],
        },
    )
    tui = InteractiveOperatorTui(OperatorState(endpoint="http://localhost:5000"))

    chat = get_chat_state(tui._default_header_snake())

    assert chat["chat_input_history"] == ["alte frage", "neuere frage"]



def test_default_header_snake_starts_in_snake_chat_mode(monkeypatch) -> None:
    import client_surfaces.operator_tui.config.user_config_manager as ucm
    import client_surfaces.operator_tui.header_snake_mixin as hsm
    import client_surfaces.operator_tui.snake_persistence as sp
    from client_surfaces.operator_tui.chat_state import get_chat_state

    monkeypatch.setattr(ucm, "load_user_config", lambda: {})
    monkeypatch.setattr(sp, "load_tui_chat_settings", lambda *args, **kwargs: {})
    monkeypatch.setattr(hsm, "load_tui_chat_settings", lambda *args, **kwargs: {})
    monkeypatch.delenv("ANANTA_TUI_SNAKE_MODE", raising=False)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_TUTORIAL_AI", raising=False)
    tui = InteractiveOperatorTui(OperatorState(endpoint="http://localhost:5000"))

    game = tui._default_header_snake()
    chat = get_chat_state(game)

    assert game.get("active") is True
    assert game.get("ui_steering") is True
    assert game.get("free_mode") is True
    assert game.get("tutorial_mode") is False
    assert game.get("ai_snake_mode") == "off"
    assert game.get("chat_panel_open") is True
    assert chat.get("active_channel") == "ai:tutor"
    assert chat.get("chat_focus") is True



def test_toggle_chat_panel_persists_setting(tmp_path, monkeypatch) -> None:
    import client_surfaces.operator_tui.snake_persistence as sp

    monkeypatch.setattr(sp, "_config_dir", lambda: tmp_path / "ananta")
    tui = InteractiveOperatorTui(OperatorState(endpoint="http://localhost:5000"))

    tui._toggle_chat_panel_open()
    persisted = load_tui_chat_settings(cwd=Path.cwd())

    assert isinstance(persisted.get("chat_panel_open"), bool)



def test_toggle_tutorial_mode_persists_setting(tmp_path, monkeypatch) -> None:
    import client_surfaces.operator_tui.snake_persistence as sp

    monkeypatch.setattr(sp, "_config_dir", lambda: tmp_path / "ananta")
    tui = InteractiveOperatorTui(OperatorState(endpoint="http://localhost:5000"))

    tui._toggle_tutorial_ai_mode()
    persisted = load_tui_chat_settings(cwd=Path.cwd())

    assert isinstance(persisted.get("tutorial_mode"), bool)



def test_keybinding_conflicts_detect_duplicate_keys(tmp_path, monkeypatch) -> None:
    cfg_path = tmp_path / "bindings.json"
    cfg_path.write_text(
        json.dumps(
            {
                "bindings": [
                    {"action": "a_one", "key": "c-w", "display": "Ctrl+W", "label": "one", "areas": ["shortcuts"]},
                    {"action": "a_two", "key": "c-w", "display": "Ctrl+W", "label": "two", "areas": ["shortcuts"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANANTA_TUI_KEYBINDINGS_FILE", str(cfg_path))
    import client_surfaces.operator_tui.keybindings_config as kb

    reload(kb)
    conflicts = kb.keybinding_conflicts()

    assert conflicts
    assert conflicts[0]["key"] == "c-w"
    assert set(conflicts[0]["actions"]) == {"a_one", "a_two"}



def test_refresh_chat_backend_models_worker_tries_lmstudio_candidates(monkeypatch) -> None:
    game: dict[str, object] = {
        "chat_backend": "ananta-worker",
        "chat_backend_api_base": "http://localhost:1234/v1",
        "chat_backend_models": [],
        "chat_backend_models_last_refresh_at": 0.0,
    }
    calls: list[str] = []

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"data": [{"id": "microsoft_-_phi-3.5-mini-instruct"}]}).encode("utf-8")

    def _fake_urlopen(req, timeout=0):
        url = str(getattr(req, "full_url", ""))
        calls.append(url)
        if "192.168.178.100:1234" in url and url.endswith("/v1/models"):
            return _FakeResp()
        raise OSError("unreachable")

    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        _fake_urlopen,
    )

    models, _ = refresh_chat_backend_models(game, force=True)

    assert "microsoft_-_phi-3.5-mini-instruct" in models
    assert any("192.168.178.100:1234" in call for call in calls)



def test_resolve_ask_skips_hub_probe_when_endpoint_is_lmstudio(monkeypatch) -> None:
    state = OperatorState(endpoint="http://192.168.178.100:1234/v1")
    tui = InteractiveOperatorTui(state)
    calls: list[str] = []

    def _fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        class _Resp:
            headers: dict[str, str] = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        return _Resp()

    monkeypatch.setenv("ANANTA_TUI_CHAT_STREAMING", "0")
    monkeypatch.setattr("client_surfaces.operator_tui.chat_mixin.urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setattr(tui, "_rag_context_for_question", lambda *args, **kwargs: [])
    monkeypatch.setattr(tui, "_build_active_target_excerpt", lambda: "")

    answer = tui._resolve_ask_question(
        "hi",
        depth="overview",
        hints=[],
        rag_context=[],
        question_tokens=[],
        prior_messages=[],
    )

    assert answer == "ok"
    assert calls == ["http://192.168.178.100:1234/v1/chat/completions"]



def test_resolve_ask_worker_response_is_not_clipped_to_600(monkeypatch) -> None:
    long_answer = "A" * 1200
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"chat_backend": "ananta-worker", "chat_answer_chars": 2000},
    )
    tui = InteractiveOperatorTui(state)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"answer": long_answer}).encode()

    monkeypatch.setattr("client_surfaces.operator_tui.chat_mixin.urllib.request.urlopen", lambda req, timeout=0: _Resp())
    monkeypatch.setattr(tui, "_rag_context_for_question", lambda *args, **kwargs: [])
    monkeypatch.setattr(tui, "_build_active_target_excerpt", lambda: "")
    monkeypatch.setattr(tui, "_chat_codecompass_context_for_question", lambda **kwargs: [])

    answer = tui._resolve_ask_question(
        "hi",
        depth="overview",
        hints=[],
        rag_context=[],
        question_tokens=[],
        prior_messages=[],
    )

    assert len(answer) == 1200



def test_terminal_context_shortcut_prepares_ai_chat_context() -> None:
    state = OperatorState(endpoint="http://localhost:5000", section_id="tasks")
    tui = InteractiveOperatorTui(state)
    tui._rendered_text = "\x1b[31mNAV\x1b[0m\nCONTENT\nCtrl+H hide help\nCtrl+K Terminal als AI-Kontext"

    tui._send_terminal_context_to_ai()

    game = tui.state.header_logo_game or {}
    chat = game.get("chat_state") or {}
    artifact_chat = game.get("artifact_chat_state") or {}
    active_target = artifact_chat.get("active_target") or {}

    assert "CONTENT" in str(game.get("ai_terminal_context") or "")
    assert active_target.get("kind") == "terminal_snapshot"
    assert chat.get("active_channel") == "ai:tutor"
    assert chat.get("chat_focus") is True
    assert tui._chat_focus_active() is True
    assert game.get("tutor_ask_question") is None
    assert "Frage im AI-Chat" in tui.state.status_message

    tui._chat_append("?")
    chat = (tui.state.header_logo_game or {}).get("chat_state") or {}
    assert chat.get("chat_input_buffer") == "?"



def test_escape_resets_to_start_state_from_input_modes() -> None:
    game = {
        "active": True,
        "ui_steering": True,
        "free_mode": True,
        "chat_panel_open": True,
        "artifact_chat_focus": True,
        "ai_snake_config_open": True,
        "ai_snake_config_combo": {"open": True, "key": "chat_backend"},
        "chat_state": {
            "chat_focus": True,
            "chat_input_buffer": "hello",
            "chat_input_cursor": 5,
        },
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="tasks",
        focus=FocusPane.DETAIL,
        mode=OperatorMode.COMMAND,
        command_line=":help",
        header_logo_game=game,
    )
    tui = InteractiveOperatorTui(state)
    tui._command_buffer = ":help"
    tui._command_cursor = len(tui._command_buffer)

    tui._escape_to_start_state()

    updated = tui.state.header_logo_game or {}
    chat = dict(updated.get("chat_state") or {})
    assert tui.state.mode is OperatorMode.NORMAL
    assert tui.state.focus is FocusPane.NAVIGATION
    assert tui.state.section_id == "tasks"
    assert tui.state.selected_index >= 0
    assert tui.state.command_line == ""
    assert updated.get("active") is False
    assert updated.get("ui_steering") is False
    assert updated.get("free_mode") is False
    assert updated.get("artifact_chat_focus") is False
    assert updated.get("ai_snake_config_open") is False
    assert bool(dict(updated.get("ai_snake_config_combo") or {}).get("open")) is False
    assert chat.get("chat_focus") is False
    assert chat.get("chat_input_buffer") == ""
