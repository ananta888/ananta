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


def test_operator_tui_renders_first_paint_shell() -> None:
    state = load_active_section(OperatorState(endpoint="http://localhost:5000", auth_state="session_env"))

    output = render_operator_shell(state, width=96, height=22)

    assert "ananta" in output
    assert "Dashboard" in output
    assert "focus=" in output
    assert "Commands:" in output
    assert ":refresh" in output


def test_operator_tui_section_commands_update_state() -> None:
    state = OperatorState(endpoint="http://localhost:5000")

    result = execute_command(":section Tasks", state)

    assert result.handled is True
    assert result.state.section_id == "tasks"
    assert result.state.mode is OperatorMode.NORMAL


def test_operator_tui_unknown_command_is_visible() -> None:
    state = OperatorState(endpoint="http://localhost:5000")

    result = execute_command(":explode", state)

    assert result.handled is False
    assert "unknown command" in result.message
    assert "unknown command" in result.state.status_message


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


def test_operator_tui_section_aliases_and_navigation() -> None:
    assert normalize_section_id("task") == "tasks"
    assert normalize_section_id("?") == "help"
    assert move_section("dashboard", 1) == "goals"


def test_operator_tui_adapter_maps_timeout_to_local_degraded_state() -> None:
    def loader(section_id: str) -> SectionLoadResult:
        raise TimeoutError(f"{section_id} timed out")

    result = SectionAdapterRegistry(loader).load("tasks")

    assert result.section_id == "tasks"
    assert result.state is PanelState.DEGRADED
    assert "timed out" in result.message


def test_operator_tui_refresh_policy_is_section_local() -> None:
    policy = refresh_policy_for("system")

    assert policy.timeout_seconds == 1.0
    assert should_refresh(elapsed_seconds=policy.refresh_interval_seconds, policy=policy)
    assert should_refresh(elapsed_seconds=0, policy=policy, force=True)


def test_operator_tui_markdown_renderer_handles_common_blocks() -> None:
    lines = render_markdown_lines("# Title\n- item\n```python\nprint('x')\n```", width=40)

    assert "# Title" in lines
    assert "- item" in lines
    assert "CODE" in lines
    assert "  print('x')" in lines


def test_operator_tui_detects_and_renders_mermaid_fallback() -> None:
    blocks = detect_diagram_blocks("```mermaid\ngraph TD\nA-->B\n```")

    assert len(blocks) == 1
    assert blocks[0].kind == "mermaid"
    assert any("A -> B" in line for line in render_diagram_fallback(blocks[0]))


def test_operator_tui_detects_and_renders_plantuml_fallback() -> None:
    blocks = detect_diagram_blocks("@startuml\nAlice -> Bob\n@enduml")

    assert len(blocks) == 1
    assert blocks[0].kind == "plantuml"
    assert any("Alice -> Bob" in line for line in render_diagram_fallback(blocks[0]))


def test_operator_tui_initial_state_carries_markdown_source() -> None:
    args = Namespace(
        base_url="http://localhost:5000",
        section="artifacts",
        mode="normal",
        focus="content",
        show_help=False,
        markdown_source="# Artifact\n```mermaid\ngraph TD\nA-->B\n```",
    )

    state = load_active_section(build_initial_state(args))
    output = render_operator_shell(state, width=100, height=40)

    assert "markdown:" in output
    assert "mermaid diagram preview" in output


def test_operator_tui_markdown_source_renders_outside_artifacts_and_help() -> None:
    state = load_active_section(
        OperatorState(
            endpoint="http://localhost:5000",
            section_id="tasks",
            mode=OperatorMode.EDIT,
            markdown_source="# Inline Vim Viewer\n```py\nprint('ok')\n```",
        )
    )
    output = render_operator_shell(state, width=100, height=40)

    assert "markdown:" in output
    assert "Inline Vim Viewer" in output
    assert "print('ok')" in output


def test_operator_tui_inline_vim_open_is_default(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text("line1\nline2\n", encoding="utf-8")

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "artifacts":
            return SectionLoadResult(
                section_id="artifacts",
                state=PanelState.HEALTHY,
                payload={"items": [{"id": "a-1", "title": "sample", "path": str(sample)}]},
                message="loaded",
            )
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="artifacts", focus=FocusPane.CONTENT)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))

    opened = tui._open_selected_item_inline()
    output = render_operator_shell(tui.state, width=110, height=36)

    assert opened is True
    assert tui.state.mode is OperatorMode.EDIT
    assert "inline vim: sample.py" in tui.state.status_message
    assert "Inline Vim Viewer" in tui.state.markdown_source
    assert "line1" in tui.state.markdown_source
    assert "sample.py" in output


def test_operator_tui_detects_terminal_graphics_capabilities() -> None:
    decision = graphics_decision({"KITTY_WINDOW_ID": "1"})

    assert decision["supported"] is True
    assert "kitty" in decision["protocols"]


def test_operator_tui_read_only_goal_and_task_rows() -> None:
    goals = build_goal_rows({"items": [{"id": "G-1", "status": "todo", "title": "Goal"}]})
    tasks = build_task_rows({"items": [{"id": "T-1", "status": "todo", "agent": "alpha", "title": "Task"}]})

    assert "G-1 [todo] Goal" in goals
    assert "T-1 [todo] agent=alpha Task" in tasks


def test_operator_tui_action_dispatch_requires_confirmation_for_risky_actions() -> None:
    action = parse_action("task_execute", risk="high")

    result = dispatch_action(action)
    confirmed = dispatch_action(action, confirmed=True)

    assert result.pending_action == action
    assert "confirmation required" in result.message
    assert confirmed.accepted is True
    assert confirmed.audit_context["intent"] == "mutation_request"


def test_operator_tui_commands_manage_pending_action_and_cancel() -> None:
    state = OperatorState(endpoint="http://localhost:5000")

    pending = execute_command(":action task_execute high", state)
    confirmed = execute_command(":confirm", pending.state)
    cancelled = execute_command(":cancel", pending.state)

    assert pending.state.pending_action is not None
    assert confirmed.state.pending_action is None
    assert cancelled.state.pending_action is None
    assert cancelled.state.mode is OperatorMode.NORMAL


def test_operator_tui_browser_fallback_url_is_section_aware() -> None:
    assert browser_fallback_url("http://localhost:5000", "tasks", "T-1") == "http://localhost:5000/tasks?target=T-1"


def test_operator_tui_fixture_smoke_detects_first_paint() -> None:
    args = Namespace(
        base_url="http://localhost:5000",
        section="dashboard",
        mode="normal",
        focus="navigation",
        show_help=False,
        markdown_source="",
    )

    result = run_fixture_smoke(args)

    assert result.ok is True
    assert "first_paint" in result.checks


def test_operator_tui_performance_measurement_reports_budget() -> None:
    result = measure("noop", 100.0, lambda: "ok")

    assert result.name == "noop"
    assert result.ok is True


def test_operator_tui_rollout_controls_are_explicit() -> None:
    assert operator_tui_enabled({"ANANTA_OPERATOR_TUI_ENABLED": "0"}) is False
    assert rollout_stage({"ANANTA_OPERATOR_TUI_STAGE": "advanced_opt_in"}) == "advanced_opt_in"
    assert "legacy" in rollback_hint()


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


def test_tab_focus_header_does_not_auto_activate_snake_mode() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state)

    tui._move_focus(-1)  # NAV -> HEADER

    game = tui.state.header_logo_game or {}
    assert tui.state.focus is FocusPane.HEADER
    assert game.get("active") is not True
    assert tui._try_header_snake_direction((0, -1)) is False


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


def test_snake_mode_toggle_enables_and_disables_frame_mode() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER)
    tui = InteractiveOperatorTui(state)

    tui._toggle_snake_mode()
    on_game = tui.state.header_logo_game or {}
    assert on_game.get("active") is True
    assert on_game.get("ui_steering") is True
    assert on_game.get("free_mode") is True

    tui._toggle_snake_mode()
    off_game = tui.state.header_logo_game or {}
    # Exiting snake mode restores the ambient AI snake (active=tutorial_default env)
    assert off_game.get("ui_steering") is False
    assert off_game.get("free_mode") is False
    assert off_game.get("tutorial_mode") is not None  # restored to env default


def test_tutorial_ai_toggle_changes_mode_flag() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER)
    tui = InteractiveOperatorTui(state)
    tui._toggle_snake_mode()

    initial = bool((tui.state.header_logo_game or {}).get("tutorial_mode"))
    tui._toggle_tutorial_ai_mode()
    toggled = bool((tui.state.header_logo_game or {}).get("tutorial_mode"))
    assert toggled is (not initial)

    tui._toggle_tutorial_ai_mode()
    restored = bool((tui.state.header_logo_game or {}).get("tutorial_mode"))
    assert restored is initial


def test_tutorial_ai_snake_is_added_with_knowledge_message() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "tutorial_mode": True,
        "local_snake_id": "s1",
        "snake": [(6, 3), (5, 3), (4, 3)],
        "trail_path": [(6, 3), (5, 3), (4, 3)],
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
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER)

    tui._tick_header_snake()

    snakes = ((tui.state.header_logo_game or {}).get("snakes") or {})
    ai = snakes.get("s-ai") if isinstance(snakes, dict) else None
    assert isinstance(ai, dict)
    assert ai.get("pseudonym") == "tutor-ai"
    assert str(ai.get("message") or "") != ""
    assert ai.get("oidc_provider") == "codecompass-ai"


def test_tutorial_ai_tip_uses_codecompass_hints_when_available(monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_TUI_VISUAL_AI_USE_CODECOMPASS", "1")
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="tasks")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["method · plan_tasks · client_surfaces/operator_tui/interactive.py"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: [])
    monkeypatch.setattr(tui, "_tutorial_ai_llm_message", lambda now, status, hints: None)

    tip = tui._tutorial_ai_tip(now=1.0)

    assert "CodeCompass:" in tip
    assert "mode=normal" in tip
    assert "section=tasks" in tip


def test_snake_access_command_updates_remote_permission_levels() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={
            "local_snake_id": "s1",
            "snakes": {
                "s1": {"id": "s1", "pseudonym": "alice", "snake_color": "mint"},
                "s2": {"id": "s2", "pseudonym": "bob", "snake_color": "violet"},
            },
        },
    )

    result = execute_command(":snake-access s2 full", state)
    game = result.state.header_logo_game or {}
    access = dict(game.get("remote_access") or {})
    snakes = dict(game.get("snakes") or {})

    assert result.handled is True
    assert access.get("s2") == "full"
    assert dict(snakes.get("s2") or {}).get("access_level") == "full"


def test_inactive_header_shows_logo_and_hides_snake_explanation() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION, header_logo_game={})
    output = render_operator_shell(state, width=120, height=28)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    header = "\n".join(plain.splitlines()[:8])

    assert "Ctrl+S startet Snake-Modus" not in header
    assert "Freigaben: :snake-access" not in header


def test_tutorial_ai_tip_prefers_llm_when_available(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="tasks")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["hint-a"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["rag-context"])
    monkeypatch.setattr(tui, "_tutorial_ai_llm_message", lambda now, status, hints: "LLM tutor hint")

    tip = tui._tutorial_ai_tip(now=2.0)

    assert tip == "LLM tutor hint"


def test_tutorial_ai_llm_message_reads_openai_compatible_endpoint(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)

    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_MODEL", "gpt-test")
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_API_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_API_TOKEN", "secret-token")

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"Use [Tab] to switch focus."}}]}'

    monkeypatch.setattr("client_surfaces.operator_tui.interactive.urllib.request.urlopen", lambda req, timeout=0: _FakeResp())

    tip = tui._tutorial_ai_llm_message(now=1.0, status="status", hints=["hint"])

    assert tip == "Use [Tab] to switch focus."


def test_tutorial_ai_llm_message_uses_lmstudio_defaults_without_token(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_MODEL", raising=False)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_API_BASE_URL", raising=False)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)

    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"Use :inspect for details."}}]}'

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["authorization"] = req.headers.get("Authorization")
        captured["body"] = req.data.decode("utf-8")
        return _FakeResp()

    monkeypatch.setattr("client_surfaces.operator_tui.interactive.urllib.request.urlopen", _fake_urlopen)

    tip = tui._tutorial_ai_llm_message(now=1.0, status="status", hints=["hint"])

    assert tip == "Use :inspect for details."
    assert captured["url"] == "http://192.168.178.100:1234/v1/chat/completions"
    assert captured["authorization"] in {None, ""}
    assert '"model": "google/gemma-4-e4b"' in captured["body"]


def test_tutorial_ai_llm_training_mode_selects_tagged_profile(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_MODEL", "meta-llama_-_llama-3.2-1b-instruct")
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_API_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_TRAINING", "1")

    calls = {"count": 0}

    class _FakeResp:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._payload

    def _fake_urlopen(req, timeout=0):
        calls["count"] += 1
        body = req.data.decode("utf-8")
        if "steering prefix" in body:
            return _FakeResp(b'{"choices":[{"message":{"content":"[target=nav] Open tasks and inspect queue."}}]}')
        return _FakeResp(b'{"choices":[{"message":{"content":"Open tasks and inspect queue."}}]}')

    monkeypatch.setattr("client_surfaces.operator_tui.interactive.urllib.request.urlopen", _fake_urlopen)

    tip = tui._tutorial_ai_llm_message(now=1.0, status="status", hints=["hint"])

    assert tip == "Open tasks and inspect queue."
    assert tui._tutorial_worker_target_hint == "nav"
    assert tui._tutorial_last_target == "nav"
    assert calls["count"] >= 3


def test_tutorial_ai_tip_async_mode_keeps_ui_responsive(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="dashboard")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_tutorial_async_enabled", lambda: True)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["queue depth"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["tasks pending"])
    monkeypatch.setattr(tui, "_tutorial_ai_worker_propose_message", lambda now, status, hints, rag_context: None)

    def _slow_llm(*, now: float, status: str, hints: list[str]) -> str | None:
        time.sleep(0.15)
        tui._tutorial_worker_target_hint = "nav"
        return "Open tasks and inspect queue."

    monkeypatch.setattr(tui, "_tutorial_ai_llm_message", _slow_llm)

    first_tip = tui._tutorial_ai_tip(now=1.0)
    assert "analysiert UI-Delta" in first_tip

    time.sleep(0.2)
    second_tip = tui._tutorial_ai_tip(now=2.0)
    assert "Open tasks and inspect queue." in second_tip
    assert tui._tutorial_last_target == "nav"


def test_tutorial_ai_worker_propose_message_reads_step_propose(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_BACKEND", "worker-propose")

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"status":"success","data":{"reason":"[target=nav] Open tasks and inspect failed item first."}}'

    monkeypatch.setattr("client_surfaces.operator_tui.interactive.urllib.request.urlopen", lambda req, timeout=0: _FakeResp())

    tip = tui._tutorial_ai_worker_propose_message(
        now=1.0,
        status="status",
        hints=["hint-a"],
        rag_context=["rag-a"],
    )

    assert tip == "Open tasks and inspect failed item first."
    assert tui._tutorial_worker_target_hint == "nav"


def test_tutorial_ai_tip_includes_rag_helper_context_when_llm_missing(monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_TUI_VISUAL_AI_USE_CODECOMPASS", "1")
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="tasks")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: [])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["architecture · Hub owns routing"])
    monkeypatch.setattr(tui, "_tutorial_ai_llm_message", lambda now, status, hints: None)

    tip = tui._tutorial_ai_tip(now=2.0)

    assert "RAG:" in tip
    assert "Hub owns routing" in tip


def test_tutorial_ai_target_cell_prefers_header_for_auth_context() -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)

    target = tui._tutorial_ai_target_cell(
        board_w=120,
        board_h=30,
        context_tokens=["auth endpoint oidc header"],
        local_head=(50, 12),
    )

    assert target[0] >= 90
    assert target[1] <= 6


def test_tutorial_ai_snake_moves_toward_context_target(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "tutorial_mode": True,
        "local_snake_id": "s1",
        "snake": [(10, 10), (9, 10), (8, 10)],
        "trail_path": [(10, 10), (9, 10), (8, 10)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["auth endpoint header"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["oidc configuration"])
    monkeypatch.setattr(tui, "_tutorial_ai_tip", lambda now: "tip")

    snakes = {
        "s1": {
            "id": "s1",
            "snake": [(10, 10), (9, 10)],
            "trail_path": [(10, 10), (9, 10)],
            "message": "",
            "snake_color": "mint",
        }
    }
    tui._update_tutorial_ai_snake(game, snakes, now=10.0, board_w=120, board_h=30, enabled=True)

    ai = snakes.get("s-ai")
    assert isinstance(ai, dict)
    head = (ai.get("snake") or [(-1, -1)])[0]
    target = ai.get("target_cell")
    assert isinstance(target, tuple)
    assert isinstance(head, tuple)
    assert head[0] >= 100


def test_tutorial_ai_propose_history_is_recorded_in_game_state(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "tutorial_mode": True,
        "local_snake_id": "s1",
        "snake": [(10, 10), (9, 10), (8, 10)],
        "trail_path": [(10, 10), (9, 10), (8, 10)],
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["task navigation"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["queue status"])
    monkeypatch.setattr(tui, "_tutorial_ai_tip", lambda now: "Open tasks and inspect queue.")
    tui._tutorial_last_source = "worker-propose"
    tui._tutorial_last_target = "nav"

    snakes = {
        "s1": {
            "id": "s1",
            "snake": [(10, 10), (9, 10)],
            "trail_path": [(10, 10), (9, 10)],
            "message": "",
            "snake_color": "mint",
        }
    }
    tui._update_tutorial_ai_snake(game, snakes, now=4.0, board_w=120, board_h=30, enabled=True)

    history = game.get("tutorial_propose_history")
    assert isinstance(history, list)
    assert history
    latest = history[-1]
    assert isinstance(latest, dict)
    assert latest.get("source") == "worker-propose"
    assert latest.get("target") == "nav"
    assert "inspect queue" in str(latest.get("text") or "")


def test_dashboard_shows_tutorial_ai_propose_history_in_snake_mode() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="dashboard",
        focus=FocusPane.CONTENT,
        panel_states={"dashboard": PanelState.HEALTHY},
        section_payloads={"dashboard": {"queue": {"depth": 2}}},
        header_logo_game={
            "active": True,
            "tutorial_mode": True,
            "tutorial_propose_history": [
                {"source": "worker-propose", "target": "header", "text": "Check endpoint and auth first."},
                {"source": "openai-compatible", "target": "nav", "text": "Now move to Tasks section."},
            ],
        },
    )

    output = render_operator_shell(state, width=118, height=34)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)

    assert "AI Flow:" in plain
    assert "worker-propose->header" in plain
    assert "openai-compatible->nav" in plain
    assert plain.index("AI Flow:") < plain.index("focus=")


def test_dashboard_shows_tutorial_ai_propose_history_when_snake_inactive() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="dashboard",
        focus=FocusPane.CONTENT,
        panel_states={"dashboard": PanelState.HEALTHY},
        section_payloads={"dashboard": {"queue": {"depth": 2}}},
        header_logo_game={
            "active": False,
            "tutorial_mode": False,
            "tutorial_propose_history": [
                {"source": "worker-propose", "target": "content", "text": "I explain the currently selected panel."},
            ],
        },
    )

    output = render_operator_shell(state, width=118, height=34)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)

    assert "AI Flow:" in plain
    assert "worker-propose->content" in plain


def test_snake_message_style_and_color_can_cycle() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER)
    tui = InteractiveOperatorTui(state)
    tui._toggle_snake_mode()

    before = dict(tui.state.header_logo_game or {})
    tui._snake_cycle_message_style()
    tui._snake_cycle_color()
    after = dict(tui.state.header_logo_game or {})

    assert before.get("message_style") != after.get("message_style")
    assert before.get("snake_color") != after.get("snake_color")


def test_snake_mode_does_not_auto_switch_focus_or_section() -> None:
    game = {
        "active": True,
        "alive": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(17, 2), (16, 2), (15, 2)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "score": 0,
        "moves": 0,
        "last_move": 0.0,
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.HEADER,
        header_logo_game=game,
    )
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER, section_id="dashboard")

    tui._tick_header_snake()

    assert tui.state.focus is FocusPane.HEADER
    assert tui.state.section_id == "dashboard"
    assert (tui.state.header_logo_game or {}).get("active") is True


def test_snake_tick_keeps_manual_ui_state_stable() -> None:
    game = {
        "active": True,
        "alive": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(17, 2), (16, 2), (15, 2)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "score": 0,
        "moves": 0,
        "last_move": 0.0,
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.HEADER,
        header_logo_game=game,
    )
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.NAVIGATION, section_id="tasks", selected_index=3)
    tui._tick_header_snake()
    assert tui.state.focus is FocusPane.NAVIGATION
    assert tui.state.section_id == "tasks"
    assert tui.state.selected_index == 3


def test_snake_does_not_switch_to_detail_by_position() -> None:
    game = {
        "active": True,
        "alive": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(4, 3), (3, 3), (2, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "score": 0,
        "moves": 1,
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER, section_id="dashboard")

    tui._tick_header_snake()

    assert tui.state.focus is FocusPane.HEADER
    assert tui.state.section_id == "dashboard"


def test_snake_remains_drivable_after_escape_outside_header_focus() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(17, 2), (16, 2), (15, 2)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "score": 0,
        "moves": 0,
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.NAVIGATION)

    assert tui._try_header_snake_direction((0, 1)) is True
    assert (tui.state.header_logo_game or {}).get("next_direction") == (0, 1)


def test_snake_wraps_at_screen_border_and_stays_alive() -> None:
    head = (119, 2)
    game = {
        "active": True,
        "alive": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [head, (max(0, head[0] - 1), head[1]), (max(0, head[0] - 2), head[1])],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "boxes": [],
        "score": 0,
        "moves": 1,
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER)

    tui._tick_header_snake()

    snake = (tui.state.header_logo_game or {}).get("snake") or []
    assert (tui.state.header_logo_game or {}).get("alive") is True
    assert snake and snake[0][0] != 119


def test_snake_no_longer_selects_sections_from_screen_regions() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(4, 3), (3, 3), (2, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "score": 0,
        "moves": 0,
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER, section_id="dashboard")

    tui._tick_header_snake()

    assert tui.state.section_id == "dashboard"
    assert tui.state.focus is FocusPane.HEADER


def test_snake_message_can_be_saved_to_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "message_mode": True,
        "message_draft": "Hallo Snake",
        "board_w": 18,
        "board_h": 6,
        "snake": [(6, 3), (5, 3), (4, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._snake_commit_message()

    cfg = Path(tmp_path) / ".config" / "ananta" / "snake-config.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["snake_message"] == "Hallo Snake"
    assert data["tutorial_user_feed"] == "Hallo Snake"
    assert (tui.state.header_logo_game or {}).get("message") == "Hallo Snake"
    assert (tui.state.header_logo_game or {}).get("tutorial_user_feed") == "Hallo Snake"


def test_snake_message_template_command_updates_prompt_template(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "message_mode": True,
        "message_draft": "/template Explain zone={contact_zone} using feed={user_feed}.",
        "board_w": 18,
        "board_h": 6,
        "snake": [(6, 3), (5, 3), (4, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._snake_commit_message()

    g = tui.state.header_logo_game or {}
    assert "Explain zone={contact_zone}" in str(g.get("tutorial_prompt_template") or "")
    assert "template set" in str(g.get("message") or "")


def test_tutorial_ai_tip_sync_includes_user_feed_and_contact_zone(monkeypatch) -> None:
    game = {
        "active": True,
        "tutorial_mode": True,
        "tutorial_user_feed": "Explain authentication panel",
        "tutorial_ai_local_contact": True,
        "tutorial_ai_contact_zone": "header",
        "tutorial_prompt_template": "Priority={priority}; Feed={user_feed}; Zone={contact_zone}",
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="dashboard", header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    captured = {}

    def _fake_llm(*, now: float, status: str, hints: list[str]) -> str | None:
        captured["status"] = status
        return None

    monkeypatch.setattr(tui, "_tutorial_ai_worker_propose_message", lambda now, status, hints, rag_context: None)
    monkeypatch.setattr(tui, "_tutorial_ai_llm_message", _fake_llm)

    result = tui._tutorial_ai_tip_sync(now=1.0, status="base-status", hints=["h1"], rag_context=["r1"])

    assert result["source"] == "codecompass-rag"
    status = str(captured.get("status") or "")
    assert "Feed=Explain authentication panel" in status
    assert "Zone=header" in status
    assert "Priority=explain-current-position" in status


def test_tutorial_rag_context_prefers_operator_tui_graph_and_embedding_records(tmp_path, monkeypatch) -> None:
    out_dir = tmp_path / "rag-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.jsonl").write_text(
        json.dumps({"kind": "function_symbol", "file": "client_surfaces/operator_tui/interactive.py", "name": "_tutorial_ai_tip"}) + "\n",
        encoding="utf-8",
    )
    (out_dir / "embedding.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "embedding_record",
                        "file": "client_surfaces/operator_tui/interactive.py",
                        "embedding_text": "snake prompt template propose flow",
                    }
                ),
                json.dumps(
                    {
                        "kind": "embedding_record",
                        "file": "src/unrelated.py",
                        "embedding_text": "completely unrelated",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "graph_nodes.jsonl").write_text(
        json.dumps(
            {
                "kind": "graph_node",
                "name": "_update_tutorial_ai_snake",
                "file": "client_surfaces/operator_tui/interactive.py",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "graph_edges.jsonl").write_text(
        json.dumps(
            {
                "kind": "graph_edge",
                "relation": "calls",
                "source_path": "client_surfaces/operator_tui/interactive.py",
                "target_path": "client_surfaces/operator_tui/renderer.py",
                "source_id": "n1",
                "target_id": "n2",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "partitioned_outputs": {
                    "embedding": ["embedding.jsonl"],
                    "graph_nodes": ["graph_nodes.jsonl"],
                    "graph_edges": ["graph_edges.jsonl"],
                }
            }
        ),
        encoding="utf-8",
    )

    from client_surfaces.operator_tui.tutorial_ai_mixin import _load_rag_context_from_dir

    query_tokens = ["snake", "prompt", "flow", "explain"]
    context = _load_rag_context_from_dir(out_dir, query_tokens, 48, 800)

    joined = "\n".join(context)
    assert "embedding" in joined
    assert "graph_nodes" in joined
    assert "graph_edges" in joined
    assert "client_surfaces/operator_tui/interactive.py" in joined


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


def test_snake_message_mode_typing_and_backspace() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(6, 3), (5, 3), (4, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._toggle_snake_message_mode()
    tui._snake_message_append("A")
    tui._snake_message_append("B")
    tui._snake_message_backspace()

    g = tui.state.header_logo_game or {}
    assert g.get("message_mode") is True
    assert g.get("message_draft") == "A"


def test_snake_message_mode_accepts_command_bound_letters() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "message_mode": True,
        "message_draft": "",
        "board_w": 18,
        "board_h": 6,
        "snake": [(6, 3), (5, 3), (4, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._normal_or_text("e", lambda: None)
    tui._normal_or_text("m", lambda: None)

    g = tui.state.header_logo_game or {}
    assert g.get("message_mode") is True
    assert g.get("message_draft") == "em"


def test_fullscreen_snake_overlay_renders_message_tail_and_text_marking(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 0.0)
    lines = ["abcdefghij" + " " * 30] + [" " * 40] * 19
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0), (2, 0), (3, 0), (4, 0)],
        "mark_cells": [(5, 0, 8)],
        "message": "HI",
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=40)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[0])

    assert plain[2] == "H"
    assert plain[3] == "I"
    assert plain[5] == "f"


def test_trail_message_window_and_speed_scroll_over_full_text(monkeypatch) -> None:
    times = iter([0.0, 2.0])
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: next(times))
    lines = [" " * 40] * 20
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0)],
        "message": "ABCDE",
        "message_style": "trail",
        "snake_message_effect_enabled": True,
        "trail_window": 3,
        "trail_speed": 1.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out1 = _overlay_fullscreen_snake(lines, state, width=40)
    plain1 = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out1[0])
    out2 = _overlay_fullscreen_snake(lines, state, width=40)
    plain2 = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out2[0])

    assert plain1[2:5] == "ABC"
    assert plain2[2:5] == "CDE"


def test_trail_message_remains_visible_when_snake_stops(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 1.0)
    lines = [" " * 40] * 20
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0)],  # no extra movement trail
        "message": "HELLO",
        "message_style": "trail",
        "snake_message_effect_enabled": True,
        "trail_window": 5,
        "trail_speed": 1.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=40)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[0])

    letters = "".join(ch for ch in plain if ch.isalpha())
    assert len(letters) >= 4


def test_trail_message_translates_newlines_for_display_only(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 0.0)
    lines = [" " * 40] * 20
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0), (2, 0), (3, 0), (4, 0), (5, 0)],
        "message": "A\nB",
        "message_style": "trail",
        "snake_message_effect_enabled": True,
        "trail_window": 4,
        "trail_speed": 1.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=40)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[0])

    assert "⏎" in plain


def test_snake_message_effect_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 0.0)
    lines = [" " * 40] * 20
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0), (2, 0), (3, 0)],
        "message": "SHOULD_NOT_RENDER",
        "message_style": "ticker",
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=40)
    plain = "\n".join(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", row) for row in out)

    assert "SHOULD_NOT_RENDER" not in plain


def test_fullscreen_overlay_renders_peer_snake_from_multi_snake_state(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 0.0)
    lines = [" " * 20 for _ in range(5)]
    game = {
        "active": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(1, 1)],
        "trail_path": [(1, 1)],
        "snakes": {
            "s1": {
                "id": "s1",
                "snake": [(1, 1)],
                "trail_path": [(1, 1)],
                "message": "",
                "snake_color": "mint",
                "message_style": "trail",
            },
            "s2": {
                "id": "s2",
                "snake": [(5, 1), (4, 1)],
                "trail_path": [(5, 1), (4, 1), (3, 1)],
                "selection_cells": [(7, 1)],
                "message": "peer",
                "snake_color": "violet",
                "message_style": "trail",
            },
        },
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)
    out = _overlay_fullscreen_snake(lines, state, width=20)
    plain_row = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[1])

    assert plain_row[5] != " "
    assert "\x1b[48;2;212;176;255m" in out[1]


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


def test_snake_tick_populates_local_snapshot_for_collab_state() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(6, 3), (5, 3), (4, 3)],
        "trail_path": [(6, 3), (5, 3), (4, 3)],
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
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER)

    tui._tick_header_snake()

    snakes = ((tui.state.header_logo_game or {}).get("snakes") or {})
    local = snakes.get("s1") if isinstance(snakes, dict) else None
    assert isinstance(local, dict)
    assert local.get("local") is True


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


def test_split_snake_playfield_wraps_before_right_detail_area() -> None:
    lines = [" " * 120 for _ in range(32)]
    game = {
        "active": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(118, 4), (117, 4), (116, 4)],
        "trail_path": [(118, 4), (117, 4), (116, 4)],
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=120)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[4])

    assert plain[118] == " "
    assert plain[32] in {"●", "◉", "·"}


def test_split_snake_chat_panel_stays_in_right_detail_slice() -> None:
    from client_surfaces.operator_tui.chat_state import append_message, default_chat_state, make_message

    chat = default_chat_state("s1")
    chat["active_channel"] = "ai:tutor"
    append_message(
        chat,
        make_message(
            channel_id="ai:tutor",
            channel_type="ai",
            sender_id="s-ai",
            sender_kind="ai",
            text="Antwort",
            delivery_state="received",
        ),
    )
    game = {
        "active": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(1, 1)],
        "trail_path": [(1, 1)],
        "chat_panel_open": True,
        "chat_state": chat,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    rendered = render_operator_shell(state, width=120, height=32)
    plain_lines = [re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line) for line in rendered.splitlines()]
    active_row = next(line for line in plain_lines if "ACTIVE: AI" in line)
    split_col = 120 - 34

    assert active_row.index("ACTIVE: AI") >= split_col + 2


def test_split_snake_chat_panel_does_not_blank_snake_under_empty_rows() -> None:
    lines = [" " * 120 for _ in range(32)]
    game = {
        "active": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(60, 20), (59, 20), (58, 20)],
        "trail_path": [(60, 20), (59, 20), (58, 20)],
        "chat_panel_open": True,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=120)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[20])

    assert plain[60] == "●"


def test_fullscreen_snake_overlay_preserves_header_and_footer_rows() -> None:
    game = {
        "active": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(118, 0), (117, 0), (116, 0)],
        "trail_path": [(118, 0), (117, 0), (116, 0)],
        "chat_panel_open": True,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    output = render_operator_shell(state, width=120, height=32)
    plain_lines = [re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line) for line in output.splitlines()]

    assert "focus=" in plain_lines[-3]
    assert plain_lines[-1].startswith("[Ctrl+W]")
    assert "ACTIVE:" not in "\n".join(plain_lines[:8])


def test_snake_copy_selection_moves_text_to_clipboard_and_message(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "snake": [(2, 3), (1, 3), (0, 3)],
        "selection_cells": [(1, 1), (2, 1), (3, 1)],
        "board_w": 30,
        "board_h": 12,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)
    monkeypatch.setattr(tui, "_snake_render_plain_lines", lambda: ["", "abcde", "", "", ""])

    tui._snake_copy_selection()

    g = tui.state.header_logo_game or {}
    assert g.get("clipboard") == "bcd"
    assert g.get("message") == "bcd"


def test_snake_copy_preserves_newlines_unchanged(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "snake": [(2, 3), (1, 3), (0, 3)],
        "selection_cells": [(0, 0), (1, 0), (0, 1), (1, 1)],
        "board_w": 30,
        "board_h": 12,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)
    monkeypatch.setattr(tui, "_snake_render_plain_lines", lambda: ["ab", "cd", "", "", ""])

    tui._snake_copy_selection()

    copied = str((tui.state.header_logo_game or {}).get("clipboard") or "")
    assert copied == "ab\ncd"


def test_snake_frame_mode_collects_multiple_regions_and_copy(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "snake": [(2, 1), (1, 1), (0, 1)],
        "selection_cells": [],
        "selection_regions": [],
        "board_w": 30,
        "board_h": 12,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._snake_toggle_frame_mode()  # anchor at (2,1)
    g = dict(tui.state.header_logo_game or {})
    g["snake"] = [(4, 2), (3, 2), (2, 2)]
    tui.state = tui.state.with_updates(header_logo_game=g)
    tui._snake_toggle_selection()  # first frame

    g = dict(tui.state.header_logo_game or {})
    g["snake"] = [(8, 2), (7, 2), (6, 2)]
    tui.state = tui.state.with_updates(header_logo_game=g)
    tui._snake_toggle_selection()  # second frame

    g = tui.state.header_logo_game or {}
    regions = g.get("selection_regions") or []
    assert len(regions) == 2
    assert len(g.get("selection_cells") or []) > 0

    monkeypatch.setattr(tui, "_snake_render_plain_lines", lambda: ["0123456789", "abcdefghij", "klmnopqrst", "", ""])
    tui._snake_copy_selection()
    copied = (tui.state.header_logo_game or {}).get("clipboard") or ""
    assert copied


def test_snake_clear_visual_marks_resets_all_selection_state() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "snake": [(2, 1), (1, 1), (0, 1)],
        "mark_cells": [(2, 1, 4)],
        "selection_anchor": (2, 1),
        "selection_cells": [(1, 1), (2, 1)],
        "selection_regions": [(1, 1, 2, 2)],
        "selection_frame_mode": True,
        "selection_frame_anchor": (1, 1),
        "snakes": {
            "s1": {"selection_cells": [(1, 1)], "mark_cells": [(1, 1, 2)], "selection_regions": [(1, 1, 1, 1)]},
            "s2": {"selection_cells": [(3, 3)], "mark_cells": [(3, 3, 2)], "selection_regions": [(3, 3, 3, 3)]},
        },
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._snake_clear_visual_marks()

    g = tui.state.header_logo_game or {}
    assert g.get("mark_cells") == []
    assert g.get("selection_cells") == []
    assert g.get("selection_regions") == []
    assert g.get("selection_frame_mode") is False
    snakes = g.get("snakes") or {}
    assert isinstance(snakes, dict)
    assert (snakes.get("s1") or {}).get("selection_cells") == []
    assert (snakes.get("s2") or {}).get("mark_cells") == []


def test_snake_replace_selection_only_in_command_line(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "snake": [(2, 3), (1, 3), (0, 3)],
        "selection_cells": [(2, 3), (3, 3)],
        "message": "ZZ",
        "board_w": 40,
        "board_h": 12,
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.HEADER,
        mode=OperatorMode.COMMAND,
        command_line="abcdef",
        header_logo_game=game,
    )
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, mode=OperatorMode.COMMAND, command_line="abcdef")
    monkeypatch.setattr(tui, "_snake_render_plain_lines", lambda: ["line0", "line1", "line2", ":abcdef", "line4"])

    tui._snake_replace_selection()

    assert tui.state.command_line == "aZZdef"


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


def test_snake_immediate_brake_sets_velocity_zero() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "vel_x": 14.0,
        "vel_y": -3.0,
        "accum_x": 0.4,
        "accum_y": 0.8,
        "board_w": 18,
        "board_h": 6,
        "snake": [(6, 3), (5, 3), (4, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._snake_immediate_brake()

    g = tui.state.header_logo_game or {}
    assert g.get("vel_x") == 0.0
    assert g.get("vel_y") == 0.0
    assert g.get("accum_x") == 0.0
    assert g.get("accum_y") == 0.0


def test_snake_hover_selection_uses_delay_before_selecting_nav() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "board_w": 120,
        "board_h": 31,
        "snake": [(2, 12), (1, 12), (0, 12)],
        "direction": (0, 1),
        "next_direction": (0, 1),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    base = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER)

    s1 = tui._apply_snake_hover_selection_delay(base, head=(2, 12), now=10.0)
    s2 = tui._apply_snake_hover_selection_delay(s1, head=(2, 12), now=10.2)
    s3 = tui._apply_snake_hover_selection_delay(s2, head=(2, 12), now=10.8)

    assert s1.focus is FocusPane.HEADER
    assert s2.focus is FocusPane.HEADER
    assert s3.focus is FocusPane.NAVIGATION


def test_chat_rag_question_tokens_and_name_lookup_prefer_function_detail(tmp_path: Path) -> None:
    out_dir = tmp_path / "rag-out"
    out_dir.mkdir()
    (out_dir / "index.jsonl").write_text(
        json.dumps({"kind": "function", "file": "src/unrelated.py", "name": "toggle_misc"}) + "\n",
        encoding="utf-8",
    )
    details = [
        {"kind": "function", "file": "src/unrelated.py", "name": "other"},
        {"kind": "function", "file": "client_surfaces/operator_tui/snake_ops_mixin.py", "name": "_toggle_snake_mode"},
        {"kind": "class", "file": "client_surfaces/operator_tui/snake_ops_mixin.py", "name": "SnakeOpsMixin"},
    ]
    (out_dir / "details.jsonl").write_text("\n".join(json.dumps(d) for d in details) + "\n", encoding="utf-8")

    from client_surfaces.operator_tui.tutorial_ai_mixin import _load_rag_context_from_dir

    context = _load_rag_context_from_dir(out_dir, ["toggle", "snake", "mode"], 5, 1, scope_filter="full")
    joined = "\n".join(context)

    assert context
    assert "snake_ops_mixin.py" in context[0]
    assert "_toggle_snake_mode" in joined

    class_context = _load_rag_context_from_dir(out_dir, ["snakeopsmixin"], 5, 1, scope_filter="full")
    assert "SnakeOpsMixin" in "\n".join(class_context)


def test_chat_llm_prompt_honors_context_char_opt_out_and_prior_messages(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    captured: dict[str, object] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    def _fake_urlopen(req, timeout):
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_API_BASE_URL", "http://lmstudio.test/v1")
    monkeypatch.setenv("ANANTA_TUI_CHAT_CONTEXT_CHARS", "500")
    monkeypatch.setenv("ANANTA_TUI_CHAT_STREAMING", "0")
    monkeypatch.setattr("client_surfaces.operator_tui.chat_mixin.urllib.request.urlopen", _fake_urlopen)

    answer = tui._tutorial_ai_llm_ask(
        question="und was bedeutet das?",
        context_text="x" * 900,
        depth="deep",
        prior_messages=[{"role": "assistant", "content": "vorherige antwort"}],
    )

    assert answer == "ok"
    body = captured["body"]
    assert isinstance(body, dict)
    messages = body["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].count("x") >= 500
    assert {"role": "assistant", "content": "vorherige antwort"} in messages
    assert body["max_tokens"] >= 400


def test_chat_channel_cycle_preserves_input_buffer() -> None:
    game = {"active": True, "alive": True, "ui_steering": True}
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()
    tui._chat_append("abc")

    tui._chat_cycle_channel()

    chat = (tui.state.header_logo_game or {}).get("chat_state") or {}
    assert chat.get("active_channel") == "ai:tutor"
    assert chat.get("chat_input_buffer") == "abc"


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


def test_chat_input_supports_cursor_backspace_delete_and_history() -> None:
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state

    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game={"active": True})
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()
    tui._chat_append("abcd")
    tui._chat_move_cursor(-2)
    tui._chat_backspace()
    tui._chat_delete()

    game = dict(tui.state.header_logo_game or {})
    chat = get_chat_state(game)
    assert chat.get("chat_input_buffer") == "ad"
    assert int(chat.get("chat_input_cursor") or 0) == 1

    chat["chat_input_buffer"] = "draft"
    chat["chat_input_cursor"] = 5
    chat["chat_input_history"] = ["eins", "zwei"]
    chat["chat_input_history_index"] = None
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game))

    tui._chat_history_move(-1)
    chat = (tui.state.header_logo_game or {}).get("chat_state") or {}
    assert chat.get("chat_input_buffer") == "zwei"
    tui._chat_history_move(-1)
    chat = (tui.state.header_logo_game or {}).get("chat_state") or {}
    assert chat.get("chat_input_buffer") == "eins"
    tui._chat_history_move(1)
    chat = (tui.state.header_logo_game or {}).get("chat_state") or {}
    assert chat.get("chat_input_buffer") == "zwei"
    tui._chat_history_move(1)
    chat = (tui.state.header_logo_game or {}).get("chat_state") or {}
    assert chat.get("chat_input_buffer") == "draft"


def test_ai_chat_send_does_not_pause_snake() -> None:
    game = {"active": True, "alive": True, "ui_steering": True, "free_mode": True, "tutorial_mode": False}
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()
    tui._chat_cycle_channel()
    tui._chat_append("hi")

    tui._chat_send_message()

    updated = tui.state.header_logo_game or {}
    assert updated.get("paused") is not True
    assert updated.get("tutor_ask_question") == "hi"
    assert updated.get("_ask_submitted") is False
    assert updated.get("tutorial_mode") is False


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


def test_status_line_shows_visual_ai_mode_marker() -> None:
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={"tutorial_mode": False})
    output = render_operator_shell(state, width=96, height=20)
    assert "VAI:off" in output


def test_chat_panel_shows_timeout_progress_while_ai_typing() -> None:
    now = time.monotonic()
    game = {
        "chat_panel_open": True,
        "tutor_ask_at": now - 1.2,
        "tutor_ask_timeout_s": 8.0,
        "chat_state": {
            "active_channel": "ai:tutor",
            "chat_focus": True,
            "ai_typing": True,
            "channels": {
                "ai:tutor": {
                    "id": "ai:tutor",
                    "channel_type": "ai",
                    "display_name": "AI tutor-ai",
                    "messages": [{"sender_id": "s1", "sender_kind": "user", "text": "frage", "created_at": now - 2}],
                    "unread": 0,
                },
                "room:main": {"id": "room:main", "channel_type": "room", "display_name": "#room", "messages": [], "unread": 0},
                "notes:self": {"id": "notes:self", "channel_type": "notes", "display_name": "notes", "messages": [], "unread": 0},
            },
        },
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.DETAIL, section_id="tasks", header_logo_game=game)
    output = render_operator_shell(state, width=110, height=24)
    assert "timeout in" in output


def test_ai_snake_config_panel_toggles_in_middle_content() -> None:
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={"tutorial_mode": True})
    tui = InteractiveOperatorTui(state)

    tui._toggle_ai_snake_config_panel()
    output_open = render_operator_shell(tui.state, width=110, height=24)
    assert "AI-SNAKE CONFIG" in output_open
    assert "CFG:on" in output_open

    tui._toggle_ai_snake_config_panel()
    output_closed = render_operator_shell(tui.state, width=110, height=24)
    assert "CFG:on" not in output_closed


def test_ai_snake_config_selected_can_disable_visual_ai() -> None:
    game = {
        "tutorial_mode": True,
        "ai_snake_config_open": True,
        "snakes": {"s-ai": {"id": "s-ai", "pseudonym": "tutor-ai"}},
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=0, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=0)

    tui._toggle_ai_snake_config_selected()
    opened = tui.state.header_logo_game or {}
    combo = dict(opened.get("ai_snake_config_combo") or {})
    assert combo.get("open") is True

    tui._ai_snake_config_combo_append_filter("aus")
    tui._ai_snake_config_combo_commit()

    updated = tui.state.header_logo_game or {}
    assert updated.get("tutorial_mode") is False
    assert "s-ai" not in dict(updated.get("snakes") or {})


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


def test_visual_command_toggles_and_requests_view() -> None:
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={})

    on = execute_command(":visual on", state).state
    game_on = dict(on.header_logo_game or {})
    assert bool(game_on.get("visual_viewport_enabled")) is True

    requested = execute_command(":visual view snake_debug_view", on).state
    game_requested = dict(requested.header_logo_game or {})
    assert game_requested.get("visual_viewport_active_view_request") == "snake_debug_view"
    assert bool(game_requested.get("visual_viewport_enabled")) is True


def test_visual_command_rejects_unknown_view_with_known_view_list() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"visual_viewport_available_views": ["logo_animation", "snake_debug_view"]},
    )

    result = execute_command(":visual view not-real", state)

    assert result.handled is False
    assert "logo_animation" in str(result.state.status_message or "")
    assert "snake_debug_view" in str(result.state.status_message or "")


def test_visual_viewport_content_lines_render_in_center_pane() -> None:
    game = {
        "visual_viewport": {"enabled": True},
        "visual_runtime_status": {
            "active_view": "renderer_diagnostics",
            "active_renderer": "ansi_blocks",
            "active_adapter": "ansi",
        },
        "visual_viewport_frame_lines": ["[renderer_diagnostics]", "view=renderer_diagnostics"],
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, header_logo_game=game)

    output = render_operator_shell(state, width=110, height=24)

    assert "VISUAL VIEWPORT" in output
    assert "[renderer_diagnostics]" in output


def test_status_line_shows_visual_viewport_runtime_marker() -> None:
    game = {
        "visual_viewport": {"enabled": True},
        "visual_runtime_status": {
            "active_view": "snake_debug_view",
            "active_renderer": "ansi_blocks",
            "active_adapter": "ansi",
        },
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    output = render_operator_shell(state, width=110, height=24)

    assert "VVP:on" in output
    assert "vv=snake_debug_view" in output
    assert "vr=ansi_blocks" in output
    assert "va=ansi" in output


def test_operator_tui_does_not_import_opengl_renderer_on_startup() -> None:
    sys.modules.pop("client_surfaces.operator_tui.visual.renderers.opengl_offscreen_renderer", None)
    state = OperatorState(endpoint="http://localhost:5000")
    InteractiveOperatorTui(state)
    assert "client_surfaces.operator_tui.visual.renderers.opengl_offscreen_renderer" not in sys.modules


def test_ai_snake_config_open_resets_chat_focus_and_command_mode() -> None:
    from client_surfaces.operator_tui.chat_state import get_chat_state

    game = {
        "tutorial_mode": True,
        "chat_panel_open": True,
        "artifact_chat_focus": True,
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.DETAIL,
        mode=OperatorMode.COMMAND,
        command_line="chat backend list",
        header_logo_game=game,
    )
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()

    tui._toggle_ai_snake_config_panel()

    updated = tui.state.header_logo_game or {}
    chat = get_chat_state(updated)
    assert tui.state.mode is OperatorMode.NORMAL
    assert tui.state.command_line == ""
    assert tui.state.focus is FocusPane.CONTENT
    assert chat.get("chat_focus") is False
    assert updated.get("artifact_chat_focus") is False


def test_ai_snake_config_combo_enter_applies_filter_input_value() -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backends_available": ["ananta-worker", "opencode", "lmstudio"],
        "chat_backend": "ananta-worker",
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=4, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=4)

    tui._toggle_ai_snake_config_selected()
    tui._ai_snake_config_combo_append_filter("code|studio")
    tui._ai_snake_config_combo_commit()

    updated = tui.state.header_logo_game or {}
    assert updated.get("chat_backend") == "code|studio"


def test_ai_snake_config_combo_arrow_selection_applies_option() -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backends_available": ["ananta-worker", "opencode", "lmstudio"],
        "chat_backend": "ananta-worker",
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=4, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=4)

    tui._toggle_ai_snake_config_selected()
    tui._ai_snake_config_combo_move(1)
    tui._ai_snake_config_combo_commit()

    updated = tui.state.header_logo_game or {}
    assert updated.get("chat_backend") == "opencode"


def test_ai_snake_config_chat_model_combo_loads_lmstudio_models(monkeypatch) -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backend": "lmstudio",
        "chat_backend_api_base": "http://lmstudio.test/v1",
        "chat_backend_models": [],
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=5, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=5)

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "data": [
                        {"id": "qwen/qwen3-coder-30b"},
                        {"id": "google/gemma-3n-e4b"},
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        lambda req, timeout=0: _FakeResp(),
    )

    tui._toggle_ai_snake_config_selected()

    updated = tui.state.header_logo_game or {}
    models = [str(item) for item in (updated.get("chat_backend_models") or [])]
    combo = dict(updated.get("ai_snake_config_combo") or {})
    assert bool(combo.get("open")) is True
    assert "qwen/qwen3-coder-30b" in models
    assert "google/gemma-3n-e4b" in models


def test_ai_snake_config_chat_model_fetch_falls_back_to_localhost(monkeypatch) -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backend": "lmstudio",
        "chat_backend_api_base": "http://192.168.178.100:1234/v1",
        "chat_backend_models": [],
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=5, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=5)

    calls: list[str] = []

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"data": [{"id": "local/model-a"}]}).encode("utf-8")

    def _fake_urlopen(req, timeout=0):
        url = str(getattr(req, "full_url", ""))
        calls.append(url)
        if "localhost:1234" in url:
            return _FakeResp()
        raise OSError("unreachable")

    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        _fake_urlopen,
    )

    tui._toggle_ai_snake_config_selected()

    updated = tui.state.header_logo_game or {}
    models = [str(item) for item in (updated.get("chat_backend_models") or [])]
    assert "local/model-a" in models
    assert any("localhost:1234" in call for call in calls)
    assert str(updated.get("chat_backend_api_base") or "").startswith("http://localhost:1234")


def test_ai_snake_config_chat_model_fetch_includes_unloaded_lmstudio_models(monkeypatch) -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backend": "lmstudio",
        "chat_backend_api_base": "http://localhost:1234/v1",
        "chat_backend_models": [],
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=5, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=5)

    class _FakeResp:
        def __init__(self, payload: dict[str, object] | list[object]) -> None:
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    def _fake_urlopen(req, timeout=0):
        url = str(getattr(req, "full_url", ""))
        if url.endswith("/v1/models"):
            return _FakeResp({"data": [{"id": "google/gemma-4-e4b"}]})
        if url.endswith("/api/v0/models"):
            return _FakeResp(
                {
                    "data": [
                        {"id": "google/gemma-4-e4b", "loaded": True},
                        {"id": "microsoft_-_phi-3.5-mini-instruct", "loaded": False},
                    ]
                }
            )
        raise OSError("unexpected endpoint")

    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        _fake_urlopen,
    )

    tui._toggle_ai_snake_config_selected()

    updated = tui.state.header_logo_game or {}
    models = [str(item) for item in (updated.get("chat_backend_models") or [])]
    states = dict(updated.get("chat_backend_model_states") or {})
    assert "google/gemma-4-e4b" in models
    assert "microsoft_-_phi-3.5-mini-instruct" in models
    assert states.get("google/gemma-4-e4b") == "loaded"
    assert states.get("microsoft_-_phi-3.5-mini-instruct") == "not_loaded"


def test_chat_model_option_label_marks_loaded_status() -> None:
    game = {
        "chat_backend_model_states": {
            "google/gemma-4-e4b": "loaded",
            "microsoft_-_phi-3.5-mini-instruct": "not_loaded",
        }
    }

    assert chat_model_option_label(game, "google/gemma-4-e4b").endswith("[geladen]")
    assert chat_model_option_label(game, "microsoft_-_phi-3.5-mini-instruct").endswith("[nicht geladen]")
    assert chat_model_option_label(game, "unknown/model").endswith("[status unbekannt]")


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


def test_chat_codecompass_context_can_be_disabled_via_config() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"chat_use_codecompass": False},
    )
    tui = InteractiveOperatorTui(state)

    hints = tui._chat_codecompass_context_for_question(question="wie geht x")

    assert hints == []


def test_tutorial_ai_llm_ask_uses_chat_max_tokens_from_config(monkeypatch) -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"chat_context_chars": 1200, "chat_max_tokens": 900},
    )
    tui = InteractiveOperatorTui(state)
    monkeypatch.setenv("ANANTA_TUI_CHAT_STREAMING", "0")
    captured: dict[str, object] = {}

    class _FakeResp:
        headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    def _fake_urlopen(req, timeout=0):
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp()

    monkeypatch.setattr("client_surfaces.operator_tui.chat_mixin.urllib.request.urlopen", _fake_urlopen)

    answer = tui._tutorial_ai_llm_ask(question="hi", context_text="ctx", depth="overview", prior_messages=[])

    assert answer == "ok"
    assert int((captured.get("body") or {}).get("max_tokens") or 0) == 900


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


def test_chat_double_slash_toggles_middle_shortcuts() -> None:
    game = {"active": True, "alive": True, "ui_steering": True, "free_mode": True}
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()
    tui._chat_append("/")
    tui._chat_append("/")

    tui._chat_send_message()

    updated = tui.state.header_logo_game or {}
    assert updated.get("shortcut_help_middle_open") is True
    assert updated.get("tutor_ask_question") is None

    tui._chat_focus_enter()
    tui._chat_append("/")
    tui._chat_append("/")
    tui._chat_send_message()

    updated = tui.state.header_logo_game or {}
    assert updated.get("shortcut_help_middle_open") is False


def test_llm_ask_uses_lmstudio_defaults_without_explicit_env(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    for key in (
        "ANANTA_TUI_SNAKE_AI_MODEL",
        "ANANTA_TUI_SNAKE_AI_API_BASE_URL",
        "ANANTA_TUI_SNAKE_AI_API_TOKEN",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANANTA_TUI_CHAT_STREAMING", "0")
    captured: dict[str, object] = {}

    class _FakeResp:
        headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp()

    monkeypatch.setattr("client_surfaces.operator_tui.chat_mixin.urllib.request.urlopen", _fake_urlopen)

    answer = tui._tutorial_ai_llm_ask(question="hi", context_text="", depth="overview", prior_messages=[])

    assert answer == "ok"
    assert captured["url"] == "http://192.168.178.100:1234/v1/chat/completions"
    assert captured["body"]["model"] == "google/gemma-4-e4b"


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


def test_chat_focus_toggle_uses_same_shortcut_for_enter_and_exit() -> None:
    game = {"active": True, "alive": True, "ui_steering": True, "free_mode": True}
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)

    tui._toggle_chat_focus()
    assert tui._chat_focus_active() is True

    tui._toggle_chat_focus()
    assert tui._chat_focus_active() is False


def test_enter_handles_config_even_when_focus_is_not_content() -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backends_available": ["ananta-worker", "opencode", "lmstudio"],
        "chat_backend": "ananta-worker",
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.DETAIL, selected_index=4, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.DETAIL, selected_index=4)

    tui._handle_enter_key()

    updated = tui.state.header_logo_game or {}
    combo = dict(updated.get("ai_snake_config_combo") or {})
    assert tui.state.focus is FocusPane.CONTENT
    assert bool(combo.get("open")) is True


def test_enter_on_navigation_history_opens_cached_original_output() -> None:
    game = {
        "chat_long_message_history": [
            {
                "id": "answer-1",
                "channel_id": "ai:tutor",
                "sender_kind": "ai",
                "text": "Antwort " + ("lang " * 30),
                "markdown": "# Chat-Nachricht\n\nAntwort " + ("lang " * 30),
                "created_at": 10.0,
            }
        ]
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.NAVIGATION,
        selected_index=len(SECTIONS),
        header_logo_game=game,
    )
    tui = InteractiveOperatorTui(state)

    tui._handle_enter_key()

    updated = tui.state.header_logo_game or {}
    assert tui.state.focus is FocusPane.CONTENT
    assert updated["chat_long_message_plain_text"].startswith("Antwort lang")
    assert updated["markdown_stream_plain"] is True
    assert updated["markdown_mermaid_render_requested"] is False
    assert tui.state.status_message == "Chat-History: Originalausgabe"


def test_mouse_click_on_navigation_history_opens_cached_original_output(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((180, 33)),
    )
    game = {
        "active": False,
        "chat_long_message_history": [
            {
                "id": "answer-1",
                "channel_id": "ai:tutor",
                "sender_kind": "ai",
                "text": "Antwort " + ("lang " * 30),
                "markdown": "# Chat-Nachricht\n\nAntwort " + ("lang " * 30),
                "created_at": 10.0,
            }
        ],
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    history_row_y = 9 + 1 + len(SECTIONS) + 3

    tui._ingest_mouse_event(x=2, y=history_row_y, event_type="down", buttons=1, now=1.0)

    updated = tui.state.header_logo_game or {}
    assert tui.state.focus is FocusPane.CONTENT
    assert updated["chat_long_message_plain_text"].startswith("Antwort lang")
    assert updated["markdown_stream_plain"] is True
    assert updated["markdown_mermaid_render_requested"] is False
    assert tui.state.status_message == "Chat-History: Originalausgabe"


def test_mouse_click_sets_focus_for_visible_panes(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION, header_logo_game={"active": False})
    tui = InteractiveOperatorTui(state)

    tui._ingest_mouse_event(x=4, y=1, event_type="down", buttons=1, now=1.0)
    assert tui.state.focus is FocusPane.HEADER

    tui._ingest_mouse_event(x=28, y=10, event_type="down", buttons=1, now=2.0)
    assert tui.state.focus is FocusPane.CONTENT

    tui._ingest_mouse_event(x=88, y=10, event_type="down", buttons=1, now=3.0)
    assert tui.state.focus is FocusPane.DETAIL

    tui._ingest_mouse_event(x=2, y=10, event_type="down", buttons=1, now=4.0)
    assert tui.state.focus is FocusPane.NAVIGATION


def test_mouse_click_on_nav_section_loads_section_content(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION, header_logo_game={"active": False})
    tui = InteractiveOperatorTui(state)

    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=1.0)

    assert tui.state.section_id == "goals"
    assert tui.state.focus is FocusPane.NAVIGATION
    assert tui.state.selected_index == 1
    rendered = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", render_operator_shell(tui.state, width=120, height=32))
    assert "GOALS" in rendered
    assert "no goals" in rendered or "Goals" in rendered


def test_nav_section_click_leaves_chat_input_focus_and_does_not_open_artifact_overlay(monkeypatch) -> None:
    from client_surfaces.operator_tui.chat_state import get_chat_state

    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state)

    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=1.0)

    game = tui.state.header_logo_game or {}
    chat = get_chat_state(dict(game))
    assert tui.state.section_id == "goals"
    assert chat["chat_focus"] is False
    assert game.get("artifact_chat_focus") is False
    assert "artifact_chat_state" not in game or not dict(game.get("artifact_chat_state") or {}).get("active_target")


def test_nav_shortcut_leaves_chat_input_focus(monkeypatch) -> None:
    from client_surfaces.operator_tui.chat_state import get_chat_state

    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state)

    tui._set_selected_index(1)

    game = tui.state.header_logo_game or {}
    chat = get_chat_state(dict(game))
    assert tui.state.section_id == "goals"
    assert chat["chat_focus"] is False


def test_mouse_click_on_visible_footer_shortcut_executes_action(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION, header_logo_game={"active": False})
    tui = InteractiveOperatorTui(state)
    rendered = tui._render()
    tui._rendered_text = rendered
    lines = [re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line) for line in rendered.splitlines()]
    y, line = next((idx, row) for idx, row in enumerate(lines) if "Ctrl+J" in row)
    x = line.index("Ctrl+J") + 1

    tui._ingest_mouse_event(x=x, y=y, event_type="down", buttons=1, now=1.0)

    assert tui.state.selected_index == 1
    assert tui.state.section_id == "goals"


def test_mouse_click_on_visible_refresh_shortcut_executes_action(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((180, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION, header_logo_game={"active": False})
    tui = InteractiveOperatorTui(state)
    rendered = tui._render()
    tui._rendered_text = rendered
    lines = [re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line) for line in rendered.splitlines()]
    y, line = next((idx, row) for idx, row in enumerate(lines) if "Ctrl+R" in row)
    x = line.index("Ctrl+R") + 1

    tui._ingest_mouse_event(x=x, y=y, event_type="down", buttons=1, now=1.0)

    assert tui.state.refresh_count == 1


def test_enter_command_mode_from_anywhere_closes_chat_artifact_and_config() -> None:
    game = {
        "ai_snake_config_open": True,
        "ai_snake_config_combo": {"open": True, "key": "chat_backend"},
        "artifact_chat_focus": True,
        "chat_panel_open": True,
        "chat_state": {"chat_focus": True, "chat_input_buffer": "x", "chat_input_cursor": 1},
    }
    state = OperatorState(endpoint="http://localhost:5000", mode=OperatorMode.NORMAL, header_logo_game=game)
    tui = InteractiveOperatorTui(state)

    tui._enter_command_mode_from_anywhere()

    updated = tui.state.header_logo_game or {}
    chat_state = dict(updated.get("chat_state") or {})
    assert tui.state.mode is OperatorMode.COMMAND
    assert chat_state.get("chat_focus") is False
    assert updated.get("artifact_chat_focus") is False
    assert bool(dict(updated.get("ai_snake_config_combo") or {}).get("open")) is False
    assert updated.get("ai_snake_config_open") is False


def test_global_shortcut_exit_command_mode_resets_to_normal() -> None:
    state = OperatorState(endpoint="http://localhost:5000", mode=OperatorMode.COMMAND, command_line=":help")
    tui = InteractiveOperatorTui(state)
    tui._command_buffer = ":help"
    tui._command_cursor = 5

    tui._exit_command_mode_for_global_shortcut()

    assert tui.state.mode is OperatorMode.NORMAL
    assert tui.state.command_line == ""
    assert tui._command_buffer == ""


def test_ai_snake_config_includes_chat_ask_timeout_field() -> None:
    items = ai_snake_config_items({})
    keys = [str(item.get("key") or "") for item in items]
    assert "chat_ask_timeout_s" in keys


def test_ai_snake_config_includes_chat_provider_and_api_base_fields() -> None:
    items = ai_snake_config_items({})
    by_key = {str(item.get("key") or ""): dict(item) for item in items}
    assert "chat_backend" in by_key
    assert str(by_key["chat_backend"].get("label") or "") == "Chat Provider"
    assert "chat_api_base" in by_key


def test_ai_snake_config_includes_chat_context_control_fields() -> None:
    items = ai_snake_config_items({})
    keys = {str(item.get("key") or "") for item in items}
    assert "chat_use_codecompass" in keys
    assert "chat_include_local_project" in keys
    assert "chat_include_wikipedia" in keys
    assert "chat_source_pack_id" in keys
    assert "chat_context_chars" in keys
    assert "chat_max_tokens" in keys
    assert "chat_rag_top_k" in keys
    assert "chat_answer_chars" in keys


def test_ai_snake_config_applies_chat_ask_timeout_value() -> None:
    game: dict[str, object] = {}
    status = apply_ai_snake_config_value(game, key="chat_ask_timeout_s", value="90")
    assert game.get("chat_ask_timeout_s") == 90.0
    assert "90" in status


def test_ai_snake_config_applies_chat_context_settings() -> None:
    game: dict[str, object] = {}
    status_a = apply_ai_snake_config_value(game, key="chat_use_codecompass", value="AUS")
    status_b = apply_ai_snake_config_value(game, key="chat_source_pack_id", value="ananta-local-only")
    status_c = apply_ai_snake_config_value(game, key="chat_context_chars", value="6000")
    status_d = apply_ai_snake_config_value(game, key="chat_max_tokens", value="1200")
    status_e = apply_ai_snake_config_value(game, key="chat_rag_top_k", value="48")
    status_f = apply_ai_snake_config_value(game, key="chat_answer_chars", value="4000")
    assert game.get("chat_use_codecompass") is False
    assert game.get("chat_source_pack_id") == "ananta-local-only"
    assert game.get("chat_context_chars") == 6000
    assert game.get("chat_max_tokens") == 1200
    assert game.get("chat_rag_top_k") == 48
    assert game.get("chat_answer_chars") == 4000
    assert "AUS" in status_a
    assert "ananta-local-only" in status_b
    assert "6000" in status_c
    assert "1200" in status_d
    assert "48" in status_e
    assert "4000" in status_f


def test_chat_state_sanitize_text_can_keep_long_messages() -> None:
    long_text = "x" * 1500
    assert len(sanitize_text(long_text, max_len=None)) == 1500


def test_ai_snake_config_backend_switch_fetches_lmstudio_models(monkeypatch) -> None:
    game: dict[str, object] = {
        "chat_backend_api_base": "http://lmstudio.test/v1",
        "chat_backend_models": [],
    }

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"data": [{"id": "meta-llama/llama-3.2-1b-instruct"}]}).encode("utf-8")

    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        lambda req, timeout=0: _FakeResp(),
    )

    status = apply_ai_snake_config_value(game, key="chat_backend", value="lmstudio")
    models = [str(item) for item in (game.get("chat_backend_models") or [])]

    assert "meta-llama/llama-3.2-1b-instruct" in models
    assert game.get("chat_backend_model") == "meta-llama/llama-3.2-1b-instruct"
    assert "modelle" in status


def test_ai_snake_config_backend_switch_sets_ananta_worker_default_model(monkeypatch) -> None:
    game: dict[str, object] = {"chat_backend_models": []}
    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        lambda req, timeout=0: (_ for _ in ()).throw(OSError("unreachable")),
    )

    status = apply_ai_snake_config_value(game, key="chat_backend", value="ananta-worker")
    models = [str(item) for item in (game.get("chat_backend_models") or [])]

    assert "google/gemma-4-e4b" in models
    assert game.get("chat_backend_model") == "google/gemma-4-e4b"
    assert "ananta-worker" in status


def test_ai_snake_config_backend_switch_uses_opencode_default_model(monkeypatch) -> None:
    game: dict[str, object] = {"chat_backend_models": []}
    monkeypatch.delenv("ANANTA_TUI_CHAT_MODEL", raising=False)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_MODEL", raising=False)
    monkeypatch.setenv("OPENCODE_DEFAULT_MODEL", "opencode/test-model")
    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        lambda req, timeout=0: (_ for _ in ()).throw(OSError("unreachable")),
    )

    status = apply_ai_snake_config_value(game, key="chat_backend", value="opencode")
    models = [str(item) for item in (game.get("chat_backend_models") or [])]

    assert "opencode/test-model" in models
    assert game.get("chat_backend_model") == "opencode/test-model"
    assert "opencode" in status


def test_chat_backend_use_sets_default_model_for_opencode(monkeypatch) -> None:
    monkeypatch.delenv("ANANTA_TUI_CHAT_MODEL", raising=False)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_MODEL", raising=False)
    monkeypatch.setenv("OPENCODE_DEFAULT_MODEL", "opencode/cli-default")
    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        lambda req, timeout=0: (_ for _ in ()).throw(OSError("unreachable")),
    )
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={
            "chat_backends_available": ["ananta-worker", "opencode", "lmstudio"],
            "chat_backend": "ananta-worker",
            "chat_backend_model": "-",
            "chat_backend_models": [],
        },
    )

    result = execute_command(":chat backend use opencode", state)
    game = dict(result.state.header_logo_game or {})

    assert game.get("chat_backend") == "opencode"
    assert game.get("chat_backend_model") == "opencode/cli-default"


def test_chat_ask_uses_timeout_from_ai_snake_config() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"chat_ask_timeout_s": 75.0},
    )
    result = execute_command(":ask timeout test", state)
    game = dict(result.state.header_logo_game or {})
    assert float(game.get("tutor_ask_timeout_s") or 0.0) == 75.0


def test_context_help_explains_terminal_context_shortcut() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"shortcut_help_open": True},
    )

    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", render_operator_shell(state, width=160, height=24))

    assert "SHORTCUTS" in plain
    assert "Ctrl+S Snake" in plain


def test_compact_artifact_chat_input_sends_ai_question() -> None:
    game = {
        "artifact_chat_state": {
            "active_target": {"kind": "file", "label": "sample.py", "path": "sample.py", "id": "sample"},
            "messages": [],
        },
        "chat_panel_open": True,
        "tutorial_mode": False,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui._artifact_chat_focus_enter()
    tui._artifact_chat_append("Was macht das?")
    tui._artifact_chat_send_message()

    updated = tui.state.header_logo_game or {}
    assert updated.get("tutor_ask_question") == "Was macht das?"
    assert updated.get("tutorial_mode") is False
    artifact_messages = ((updated.get("artifact_chat_state") or {}).get("messages") or [])
    assert artifact_messages[-1]["source"] == "user"


def test_artifact_chat_input_supports_cursor_delete_and_history() -> None:
    game = {
        "artifact_chat_state": {"active_target": {"kind": "file", "label": "sample.py"}},
        "chat_panel_open": True,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui._artifact_chat_focus_enter()
    tui._artifact_chat_append("wxyz")
    tui._artifact_chat_move_cursor(-2)
    tui._artifact_chat_backspace()
    tui._artifact_chat_delete()
    updated = tui.state.header_logo_game or {}
    assert updated.get("artifact_chat_input") == "wz"
    assert int(updated.get("artifact_chat_cursor") or 0) == 1

    updated["artifact_chat_input"] = "draft-art"
    updated["artifact_chat_cursor"] = len("draft-art")
    updated["artifact_chat_history"] = ["alt-1", "alt-2"]
    updated["artifact_chat_history_index"] = None
    tui._set_state(tui.state.with_updates(header_logo_game=updated))
    tui._artifact_chat_history_move(-1)
    assert (tui.state.header_logo_game or {}).get("artifact_chat_input") == "alt-2"
    tui._artifact_chat_history_move(1)
    assert (tui.state.header_logo_game or {}).get("artifact_chat_input") == "draft-art"


def test_copy_chat_panel_snapshot_writes_clipboard(monkeypatch) -> None:
    from client_surfaces.operator_tui.chat_state import default_chat_state, append_message, make_message

    chat = default_chat_state("s1")
    chat["active_channel"] = "ai:tutor"
    append_message(
        chat,
        make_message(
            channel_id="ai:tutor",
            channel_type="ai",
            sender_id="s-ai",
            sender_kind="ai",
            text="copy me",
            delivery_state="received",
        ),
    )
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={"chat_state": chat, "active": True})
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_copy_to_system_clipboard", lambda text: True)

    tui._copy_chat_panel_snapshot()

    game = tui.state.header_logo_game or {}
    copied = str(game.get("clipboard") or "")
    assert "CHAT" in copied
    assert "copy me" in copied
    assert "AI-snake" in copied


def test_copy_ai_status_snapshot_writes_clipboard(monkeypatch) -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={
            "tutorial_mode": True,
            "chat_panel_open": True,
            "ai_snake_mode": "lurking_follow",
            "ai_snake_runtime_status": "running",
            "ai_snake_monitor_log": [{"event": "food_eaten", "label": "Food aufgenommen", "created_at": time.time()}],
        },
    )
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_copy_to_system_clipboard", lambda text: True)

    tui._copy_ai_status_snapshot()

    game = tui.state.header_logo_game or {}
    copied = str(game.get("clipboard") or "")
    assert "AI-SNAKE STATUS" in copied
    assert "ai_snake_mode=lurking_follow" in copied
    assert "events:" in copied


def test_copy_tui_snapshot_writes_entire_rendered_view(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000", status_message="snapshot-ready")
    tui = InteractiveOperatorTui(state)
    tui._rendered_text = "\x1b[31mHEADER\x1b[0m\nbody line\n"
    copied_values: list[str] = []
    monkeypatch.setattr(tui, "_copy_to_system_clipboard", lambda text: copied_values.append(text) or True)

    tui._copy_tui_snapshot()

    game = tui.state.header_logo_game or {}
    copied = str(game.get("clipboard") or "")
    assert copied == "HEADER\nbody line\n"
    assert copied_values == [copied]
    assert "System-Zwischenablage" in str(tui.state.status_message)


def test_save_tui_snapshot_writes_file(monkeypatch, tmp_path) -> None:
    state = OperatorState(endpoint="http://localhost:5000", status_message="snapshot-ready")
    tui = InteractiveOperatorTui(state)
    tui._rendered_text = "HEADER\nbody line\n"
    monkeypatch.setenv("ANANTA_TUI_SNAPSHOT_DIR", str(tmp_path))

    tui._save_tui_snapshot()

    game = tui.state.header_logo_game or {}
    target = Path(str(game.get("last_tui_snapshot_path") or ""))
    assert target.exists()
    assert target.parent == tmp_path
    assert target.read_text(encoding="utf-8") == "HEADER\nbody line\n"
    assert "gespeichert" in str(tui.state.status_message)


def test_prediction_comments_are_routed_to_ai_monitor_not_chat() -> None:
    from client_surfaces.operator_tui.chat_state import default_chat_state

    game = {
        "chat_state": default_chat_state("s1"),
        "ai_snake_monitor_log": [],
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    prediction = {"predicted_intent": "artifact_explain", "target_ref": "DETAIL", "confidence": 0.78}

    changed = tui._route_prediction_comment_to_monitor(
        game,
        prediction=prediction,
        now=100.0,
        quiet=False,
        forced=False,
        cooldown_seconds=20,
    )

    assert changed is True
    chat = game.get("chat_state") or {}
    ai_msgs = (((chat.get("channels") or {}).get("ai:tutor") or {}).get("messages") or [])
    assert ai_msgs == []
    monitor = game.get("ai_snake_monitor_log") or []
    assert monitor
    assert "Ich glaube, du willst zu DETAIL" in str(monitor[-1].get("label") or "")


def test_chat_panel_renders_in_detail_pane_without_overlay() -> None:
    from client_surfaces.operator_tui.chat_state import default_chat_state, make_message, append_message

    chat = default_chat_state("s1")
    chat["active_channel"] = "ai:tutor"
    append_message(
        chat,
        make_message(
            channel_id="ai:tutor",
            channel_type="ai",
            sender_id="s-ai",
            sender_kind="ai",
            text="Kurze Antwort im rechten Hauptbereich",
            delivery_state="received",
        ),
    )
    game = {
        "artifact_chat_state": {
            "active_target": {"kind": "file", "label": "sample.py", "path": "sample.py"},
            "messages": [],
        },
        "chat_panel_open": True,
        "chat_state": chat,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", render_operator_shell(state, width=100, height=24))

    assert "CHAT" in plain
    assert "ACTIVE: AI" in plain
    assert "rechten Hauptbereich" in plain
    assert "Tutorial-AI propose flow" not in plain


def test_chat_messages_use_participant_colors_and_labels() -> None:
    from client_surfaces.operator_tui.chat_state import default_chat_state, make_message, append_message

    chat = default_chat_state("s1")
    chat["active_channel"] = "room:main"
    append_message(
        chat,
        make_message(
            channel_id="room:main",
            channel_type="room",
            sender_id="s1",
            sender_kind="user",
            text="Hallo",
            delivery_state="received",
        ),
    )
    append_message(
        chat,
        make_message(
            channel_id="room:main",
            channel_type="room",
            sender_id="s-ai",
            sender_kind="ai",
            text="Bereit",
            delivery_state="received",
        ),
    )
    game = {
        "chat_panel_open": True,
        "local_snake_id": "s1",
        "snake_color": "mint",
        "pseudonym": "alice",
        "snakes": {
            "s1": {"id": "s1", "pseudonym": "alice", "snake_color": "mint"},
            "s-ai": {"id": "s-ai", "pseudonym": "tutor-ai", "snake_color": "amber"},
        },
        "chat_state": chat,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    output = render_operator_shell(state, width=100, height=24)

    assert "\x1b[38;2;170;255;210malice: Hallo\x1b[0m" in output
    assert "\x1b[38;2;255;205;130mAI-snake: Bereit\x1b[0m" in output


def test_nav_click_while_visual_viewport_active_closes_viewport(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="dashboard",
        focus=FocusPane.NAVIGATION,
    )
    tui = InteractiveOperatorTui(state)
    # Overlay viewport state onto the existing game dict (which has active=True from
    # _default_header_snake). Setting active=False would switch the renderer to
    # _load_logo_lines which tries to load an SVG not available in CI.
    game = dict(tui.state.header_logo_game or {})
    game["visual_viewport_enabled"] = True
    game["visual_viewport"] = {"enabled": True}
    game["visual_viewport_frame_lines"] = ["line1", "line2"]
    tui.state = tui.state.with_updates(header_logo_game=game)

    # Click on "Goals" row in nav (body_start=9, title row +1, Goals is index 1 → y=11)
    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=1.0)

    result_game = tui.state.header_logo_game or {}
    assert tui.state.section_id == "goals"
    assert result_game.get("visual_viewport_enabled") is False
    assert dict(result_game.get("visual_viewport") or {}).get("enabled") is False
    # _sync_visual_viewport_state pops frame_lines when disabled — this is intentional
    assert "visual_viewport_frame_lines" not in result_game


def test_keyboard_nav_while_visual_viewport_active_closes_viewport(monkeypatch) -> None:
    """Regression: Ctrl+J (selection_down) in nav pane must also close the visual viewport."""
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="dashboard",
        focus=FocusPane.NAVIGATION,
        selected_index=0,
    )
    tui = InteractiveOperatorTui(state)
    game = dict(tui.state.header_logo_game or {})
    game["visual_viewport_enabled"] = True
    game["visual_viewport"] = {"enabled": True}
    tui.state = tui.state.with_updates(header_logo_game=game)

    # Simulate Ctrl+J (selection_down) which calls _set_selected_index(1) → Goals
    tui._set_selected_index(1)

    result_game = tui.state.header_logo_game or {}
    assert tui.state.section_id == "goals"
    assert result_game.get("visual_viewport_enabled") is False
    assert dict(result_game.get("visual_viewport") or {}).get("enabled") is False


# ── T17: Tab-Bar Renderer ────────────────────────────────────────────────────

def test_tab_bar_line_empty_state_returns_empty() -> None:
    from client_surfaces.operator_tui.renderer import _tab_bar_line
    state = OperatorState(endpoint="http://localhost:5000")
    assert _tab_bar_line(state, 80) == ""


def test_tab_bar_line_single_tab_hidden() -> None:
    from client_surfaces.operator_tui.renderer import _tab_bar_line
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab
    state = OperatorState(endpoint="http://localhost:5000")
    state = open_or_activate_tab(state, section_id="goals", kind="section", label="Goals")
    # Tab bar only shown with ≥2 tabs
    assert _tab_bar_line(state, 80) == ""


def test_tab_bar_line_two_tabs_shows_bar() -> None:
    from client_surfaces.operator_tui.renderer import _tab_bar_line
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab
    state = OperatorState(endpoint="http://localhost:5000")
    state = open_or_activate_tab(state, section_id="dashboard", kind="section", label="Dashboard")
    state = open_or_activate_tab(state, section_id="goals", kind="section", label="Goals")
    line = _tab_bar_line(state, 80)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
    assert "Goals" in plain
    assert "×" in plain
    assert len(plain) == 80


def test_tab_bar_line_active_tab_has_invert_escape() -> None:
    from client_surfaces.operator_tui.renderer import _tab_bar_line
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab
    state = OperatorState(endpoint="http://localhost:5000")
    state = open_or_activate_tab(state, section_id="dashboard", kind="section", label="Dashboard")
    state = open_or_activate_tab(state, section_id="goals", kind="section", label="Goals")
    line = _tab_bar_line(state, 120)
    # Active (Goals) should have invert escape \x1b[7m
    assert "\x1b[7m" in line


def test_tab_bar_line_two_tabs_separated() -> None:
    from client_surfaces.operator_tui.renderer import _tab_bar_line
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab
    state = OperatorState(endpoint="http://localhost:5000")
    state = open_or_activate_tab(state, section_id="dashboard", kind="section", label="Dashboard")
    state = open_or_activate_tab(state, section_id="goals", kind="section", label="Goals")
    line = _tab_bar_line(state, 80)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
    assert "Dashboard" in plain
    assert "Goals" in plain
    assert "│" in plain


def test_tab_bar_overflow_shows_arrow() -> None:
    from client_surfaces.operator_tui.renderer import _tab_bar_line
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab
    from client_surfaces.operator_tui.sections import SECTIONS
    state = OperatorState(endpoint="http://localhost:5000")
    for sec in SECTIONS:
        state = open_or_activate_tab(state, section_id=sec.id, kind="section", label=sec.title)
    line = _tab_bar_line(state, 40)  # narrow screen forces overflow
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
    assert "›" in plain


# ── T18: RegionIndex Tab-Regionen ────────────────────────────────────────────

def test_region_index_tab_click_returns_tab_kind() -> None:
    from client_surfaces.operator_tui.region_index import build_region_index
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab
    state = OperatorState(endpoint="http://localhost:5000")
    state = open_or_activate_tab(state, section_id="dashboard", kind="section", label="Dashboard")
    state = open_or_activate_tab(state, section_id="goals", kind="section", label="Goals")
    ri = build_region_index(state, width=120, height=32)
    # Tab bar at y=9 (body_start-1 = 10-1) when ≥2 tabs
    target = ri.get_target_at(2, 9)
    assert target is not None
    assert target.kind in {"tab", "tab_close"}


def test_region_index_body_shifts_with_two_tabs() -> None:
    from client_surfaces.operator_tui.region_index import build_region_index
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab
    state_no_tabs = OperatorState(endpoint="http://localhost:5000")
    # 1 tab: no tab bar, body_start=9
    state_one_tab = open_or_activate_tab(state_no_tabs, section_id="dashboard", kind="section", label="Dashboard")
    # 2 tabs: tab bar visible, body_start=10
    state_two_tabs = open_or_activate_tab(state_one_tab, section_id="goals", kind="section", label="Goals")

    ri_one = build_region_index(state_one_tab, width=120, height=32)
    ri_two = build_region_index(state_two_tabs, width=120, height=32)

    # With 1 tab: y=9 is nav body (no tab bar)
    t_no = ri_one.get_target_at(2, 9)
    assert t_no is not None and t_no.pane in {"nav", "header"}

    # With 2 tabs: y=9 is tab bar, y=10 is nav body
    t_tab = ri_two.get_target_at(2, 9)
    assert t_tab is not None and t_tab.kind in {"tab", "tab_close", "tab_scroll_left", "tab_scroll_right"}

    t_nav = ri_two.get_target_at(2, 10)
    assert t_nav is not None and t_nav.pane in {"nav", "header"}


def test_region_index_tab_close_target() -> None:
    from client_surfaces.operator_tui.region_index import build_region_index
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab, tab_positions_for_render
    state = OperatorState(endpoint="http://localhost:5000")
    state = open_or_activate_tab(state, section_id="dashboard", kind="section", label="Dashboard")
    state = open_or_activate_tab(state, section_id="goals", kind="section", label="Goals")
    positions = tab_positions_for_render(state, width=120, y=9)
    assert positions, "should have at least one tab position"
    tp = positions[0]
    ri = build_region_index(state, width=120, height=32)
    close_target = ri.get_target_at(tp.close_x, 9)
    assert close_target is not None
    assert close_target.kind == "tab_close"
    assert close_target.payload.get("tab_id") == tp.tab_id


# ── T19: Integration Keyboard/Mouse ─────────────────────────────────────────

def test_nav_click_creates_tab(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state)

    # Dashboard tab already open from __init__; click on Goals (y=11)
    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=1.0)

    assert tui.state.section_id == "goals"
    tab_ids = [t.id for t in tui.state.open_tabs]
    assert "section:goals" in tab_ids


def test_nav_click_twice_no_duplicate_tab(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state)
    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=1.0)
    count_before = len(tui.state.open_tabs)
    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=2.0)
    assert len(tui.state.open_tabs) == count_before


def test_tab_cycle_keyboard(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state)
    # Add goals tab
    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=1.0)
    initial_tab = tui.state.active_tab_id
    tui._tab_cycle(1)
    assert tui.state.active_tab_id != initial_tab or len(tui.state.open_tabs) == 1


def test_tab_close_keyboard(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state)
    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=1.0)
    assert len(tui.state.open_tabs) >= 2
    active_before = tui.state.active_tab_id
    tui._tab_close_active()
    assert tui.state.active_tab_id != active_before


def test_tab_initial_on_startup(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    assert len(tui.state.open_tabs) >= 1
    assert tui.state.active_tab_id != ""
