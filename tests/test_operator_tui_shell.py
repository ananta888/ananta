from __future__ import annotations

import json
import re
from argparse import Namespace
from pathlib import Path

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.app import build_initial_state, load_active_section
from client_surfaces.operator_tui.actions import dispatch_action, parse_action
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
from client_surfaces.operator_tui.sections import move_section, normalize_section_id
from client_surfaces.operator_tui.smoke import run_fixture_smoke
from agent.cli.main import _run_tui


def test_operator_tui_renders_first_paint_shell() -> None:
    state = load_active_section(OperatorState(endpoint="http://localhost:5000", auth_state="session_env"))

    output = render_operator_shell(state, width=96, height=22)

    assert "Ananta Operator TUI" in output
    assert "Dashboard" in output
    assert "endpoint=http://localhost:5000" in output
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
    assert "Ananta Operator TUI" in captured.out


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

    output = render_operator_shell(state, width=100, height=24)

    assert "Snake  score=2  running" in output
    assert "[Ctrl+S] Snake" in output


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


def test_non_snake_mode_shows_passive_snake_roster_top_left_only() -> None:
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

    assert "S1 alice [mint]" in plain
    assert "S-AI tutor-ai [amber]" in plain
    assert "Snakes (OIDC / Farbe / Nachricht):" not in plain


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
    assert off_game.get("active") is False
    assert off_game.get("ui_steering") is False
    assert off_game.get("free_mode") is False


def test_tutorial_ai_toggle_changes_mode_flag() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER)
    tui = InteractiveOperatorTui(state)
    tui._toggle_snake_mode()

    tui._toggle_tutorial_ai_mode()
    on_game = tui.state.header_logo_game or {}
    assert on_game.get("tutorial_mode") is True

    tui._toggle_tutorial_ai_mode()
    off_game = tui.state.header_logo_game or {}
    assert off_game.get("tutorial_mode") is False


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
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="tasks")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["method · plan_tasks · client_surfaces/operator_tui/interactive.py"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: [])
    monkeypatch.setattr(tui, "_tutorial_ai_llm_message", lambda now, status, hints: None)

    tip = tui._tutorial_ai_tip(now=1.0)

    assert "CodeCompass:" in tip
    assert "mode=normal" in tip
    assert "section=tasks" in tip


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

    assert "Tutorial-AI propose flow" in plain
    assert "worker-propose->header" in plain
    assert "openai-compatible->nav" in plain


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

    assert "Tutorial-AI propose flow" in plain
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
    assert (tui.state.header_logo_game or {}).get("message") == "Hallo Snake"


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
    lines = ["abcdefghij"]
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0), (2, 0), (3, 0), (4, 0)],
        "mark_cells": [(5, 0, 8)],
        "message": "HI",
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=10)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[0])

    assert plain[2] == "H"
    assert plain[3] == "I"
    assert plain[5] == "f"


def test_trail_message_window_and_speed_scroll_over_full_text(monkeypatch) -> None:
    times = iter([0.0, 2.0])
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: next(times))
    lines = [" " * 20]
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0)],
        "message": "ABCDE",
        "message_style": "trail",
        "trail_window": 3,
        "trail_speed": 1.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out1 = _overlay_fullscreen_snake(lines, state, width=20)
    plain1 = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out1[0])
    out2 = _overlay_fullscreen_snake(lines, state, width=20)
    plain2 = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out2[0])

    assert plain1[2:5] == "ABC"
    assert plain2[2:5] == "CDE"


def test_trail_message_remains_visible_when_snake_stops(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 1.0)
    lines = [" " * 20]
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0)],  # no extra movement trail
        "message": "HELLO",
        "message_style": "trail",
        "trail_window": 5,
        "trail_speed": 1.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=20)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[0])

    letters = "".join(ch for ch in plain if ch.isalpha())
    assert len(letters) >= 4


def test_trail_message_translates_newlines_for_display_only(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 0.0)
    lines = [" " * 20]
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0), (2, 0), (3, 0), (4, 0), (5, 0)],
        "message": "A\nB",
        "message_style": "trail",
        "trail_window": 4,
        "trail_speed": 1.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=20)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[0])

    assert "⏎" in plain


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
