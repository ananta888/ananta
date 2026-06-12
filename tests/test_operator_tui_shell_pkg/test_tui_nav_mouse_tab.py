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


def test_tab_focus_header_does_not_auto_activate_snake_mode() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state)

    tui._move_focus(-1)  # NAV -> HEADER

    game = tui.state.header_logo_game or {}
    assert tui.state.focus is FocusPane.HEADER
    assert game.get("active") is not True
    assert tui._try_header_snake_direction((0, -1)) is False



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



def test_status_line_shows_visual_ai_mode_marker() -> None:
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={"tutorial_mode": False})
    output = render_operator_shell(state, width=96, height=20)
    assert "VAI:off" in output



def test_long_ai_chat_answer_auto_opens_full_plain_middle_view() -> None:
    long_answer = (
        "CodeCompass liefert Kontext; chat_mixin.py sendet AI-Snake-Fragen via /snake/ask "
        "an den Hub, der ananta-worker nutzt LMStudio und zeigt die Antwort in der TUI. "
        "Diese zweite Zeile bleibt vollständig im mittleren Plain-Text-Dokument erhalten."
    )
    game = {
        "chat_panel_open": True,
        "tutor_ask_answered": True,
        "tutor_ask_answer": long_answer,
        "chat_state": {
            "active_channel": "ai:tutor",
            "ai_pending_msg_channel": "ai:tutor",
            "channels": {
                "ai:tutor": {
                    "id": "ai:tutor",
                    "channel_type": "ai",
                    "display_name": "AI tutor-ai",
                    "messages": [],
                    "unread": 0,
                }
            },
        },
    }
    tui = InteractiveOperatorTui(OperatorState(endpoint="http://localhost:5000", header_logo_game=game))

    tui._tick_chat_ai_response(game)

    assert game.get("visual_viewport_enabled") is True
    assert game.get("visual_viewport_active_view_request") == "markdown_mermaid_document"
    assert game.get("markdown_stream_plain") is True
    assert game.get("markdown_mermaid_render_requested") is False
    assert game.get("chat_long_message_plain_text") == long_answer
    assert long_answer in str(game.get("chat_long_message_markdown") or "")



def test_long_chat_middle_view_ctrl_space_toggles_rendered_and_original() -> None:
    answer = "Antwort " + ("lang " * 40)
    game = {
        "visual_viewport_enabled": True,
        "visual_viewport": {"enabled": True},
        "chat_long_message_id": "answer-1",
        "chat_long_message_plain_text": answer,
        "chat_long_message_markdown": "# Chat-Nachricht\n\n" + answer,
        "markdown_stream_plain": True,
        "markdown_mermaid_render_requested": False,
        "visual_viewport_frame_lines": ["stale"],
    }
    tui = InteractiveOperatorTui(OperatorState(endpoint="http://localhost:5000", header_logo_game=game))

    tui._open_latest_long_chat_message()

    rendered = tui.state.header_logo_game or {}
    rendered_version = str(rendered.get("visual_state_version") or "")
    assert rendered["markdown_stream_plain"] is False
    assert rendered["markdown_mermaid_render_requested"] is True
    assert rendered.get("visual_viewport_frame_lines") != ["stale"]
    assert ":rendered:" in rendered_version

    tui._open_latest_long_chat_message()

    plain = tui.state.header_logo_game or {}
    assert plain["markdown_stream_plain"] is True
    assert plain["markdown_mermaid_render_requested"] is False
    assert plain.get("visual_viewport_frame_lines") != ["stale"]
    assert ":plain:" in str(plain.get("visual_state_version") or "")
    assert plain.get("visual_state_version") != rendered_version



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



def test_mouse_click_on_template_nav_item_opens_editor(monkeypatch) -> None:
    from client_surfaces.operator_tui.region_index import build_region_index

    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((180, 33)),
    )
    payload = {
        "items": [
            {"id": "tpl:a", "kind": "template", "title": "worker_v2", "prompt_preview": "Du bearbeitest...", "raw_id": "a"},
        ],
        "templates_raw": [
            {"id": "a", "name": "worker_v2", "prompt_template": "Du bearbeitest die Aufgabe: {{ task }}"},
        ],
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "templates":
            return SectionLoadResult(section_id="templates", state=PanelState.HEALTHY, payload=payload, message="loaded templates")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))
    region_index = build_region_index(tui.state, width=180, height=32)
    template_item_row_y = -1
    for y in range(9, 32):
        target = region_index.get_target_at(2, y)
        if target is not None and target.kind == "template_nav_item":
            template_item_row_y = y
            break
    assert template_item_row_y > 0

    tui._ingest_mouse_event(x=2, y=template_item_row_y, event_type="down", buttons=1, now=1.0)

    assert tui.state.focus is FocusPane.CONTENT
    assert tui.state.mode is OperatorMode.EDIT
    assert "template editor" in tui.state.status_message
    rendered = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", render_operator_shell(tui.state, width=180, height=32))
    assert "Template Editor" in rendered



def test_mouse_click_on_second_template_nav_item_switches_editor(monkeypatch) -> None:
    from client_surfaces.operator_tui.region_index import RegionTarget

    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((180, 33)),
    )
    payload = {
        "items": [
            {"id": "tpl:a", "kind": "template", "title": "worker_v2", "prompt_preview": "A...", "raw_id": "a"},
            {"id": "tpl:b", "kind": "template", "title": "reviewer_v2", "prompt_preview": "B...", "raw_id": "b"},
        ],
        "templates_raw": [
            {"id": "a", "name": "worker_v2", "prompt_template": "Du bearbeitest die Aufgabe: {{ task }}"},
            {"id": "b", "name": "reviewer_v2", "prompt_template": "Du reviewst den Patch: {{ diff }}"},
        ],
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "templates":
            return SectionLoadResult(section_id="templates", state=PanelState.HEALTHY, payload=payload, message="loaded templates")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="templates",
        focus=FocusPane.NAVIGATION,
        section_payloads={"templates": payload},
    )
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))

    class _FakeRegionIndex:
        def get_target_at(self, x: int, y: int):
            if x != 2:
                return None
            if y == 10:
                return RegionTarget(
                    kind="template_nav_item",
                    section_id="templates",
                    pane="nav",
                    label="worker_v2",
                    payload={"template_item_index": 0, "selected_index": len(SECTIONS)},
                )
            if y == 11:
                return RegionTarget(
                    kind="template_nav_item",
                    section_id="templates",
                    pane="nav",
                    label="reviewer_v2",
                    payload={"template_item_index": 1, "selected_index": len(SECTIONS) + 1},
                )
            return RegionTarget(kind="pane", section_id="templates", pane="nav", label="NAV", payload={"focus": "navigation"})

    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_event_handler.build_region_index",
        lambda state, width, height: _FakeRegionIndex(),
    )

    tui._ingest_mouse_event(x=2, y=10, event_type="down", buttons=1, now=1.0)
    editor_first = dict((tui.state.header_logo_game or {}).get("template_editor") or {})
    assert "{{ task }}" in str(editor_first.get("text") or "")

    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=2.0)
    editor_second = dict((tui.state.header_logo_game or {}).get("template_editor") or {})
    assert "{{ diff }}" in str(editor_second.get("text") or "")
    assert str(editor_second.get("text") or "") != str(editor_first.get("text") or "")



def test_mouse_click_on_audit_nav_item_opens_viewer(monkeypatch) -> None:
    from client_surfaces.operator_tui.region_index import RegionTarget

    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((180, 33)),
    )
    payload = {
        "items": [
            {"id": "audit.logs.recent", "dataset_id": "audit.logs.recent", "group": "Audit Logs", "title": "Recent Logs", "status": "ok"},
        ],
        "datasets": {"audit.logs.recent": [{"id": "evt-1", "kind": "chat"}]},
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "audit":
            return SectionLoadResult(section_id="audit", state=PanelState.HEALTHY, payload=payload, message="loaded audit")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="audit", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))

    class _FakeRegionIndex:
        def get_target_at(self, x: int, y: int):
            if x == 2 and y == 10:
                return RegionTarget(
                    kind="audit_nav_item",
                    section_id="audit",
                    pane="nav",
                    label="Recent Logs",
                    payload={"audit_item_index": 0, "selected_index": len(SECTIONS)},
                )
            return RegionTarget(kind="pane", section_id="audit", pane="nav", label="NAV", payload={"focus": "navigation"})

    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_event_handler.build_region_index",
        lambda state, width, height: _FakeRegionIndex(),
    )

    tui._ingest_mouse_event(x=2, y=10, event_type="down", buttons=1, now=1.0)
    viewer = dict((tui.state.header_logo_game or {}).get("audit_viewer") or {})
    assert tui.state.section_id == "audit"
    assert tui.state.focus is FocusPane.CONTENT
    assert bool(viewer.get("active"))
    assert "evt-1" in str(viewer.get("text") or "")



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
    tui._ingest_mouse_event(x=2, y=11, event_type="up", buttons=0, now=1.1)

    result_game = tui.state.header_logo_game or {}
    assert tui.state.section_id == "goals"
    assert result_game.get("visual_viewport_enabled") is False
    assert dict(result_game.get("visual_viewport") or {}).get("enabled") is False
    # _sync_visual_viewport_state pops frame_lines when disabled — this is intentional
    assert "visual_viewport_frame_lines" not in result_game



def test_nav_click_closes_middle_chat_viewport_even_while_ai_is_typing(monkeypatch) -> None:
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
    game = dict(tui.state.header_logo_game or {})
    game["visual_viewport_enabled"] = True
    game["visual_viewport"] = {"enabled": True}
    game["chat_long_message_id"] = "streaming"
    game["tutor_ask_question"] = "frage"
    game["tutor_ask_answered"] = False
    game["llm_streaming_partial"] = "teilantwort"
    game["chat_state"] = {"ai_typing": True}
    tui.state = tui.state.with_updates(header_logo_game=game)

    # Click on "Goals" row in nav
    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=1.0)
    tui._ingest_mouse_event(x=2, y=11, event_type="up", buttons=0, now=1.1)

    result_game = tui.state.header_logo_game or {}
    assert tui.state.section_id == "goals"
    assert result_game.get("visual_viewport_enabled") is False
    assert dict(result_game.get("visual_viewport") or {}).get("enabled") is False



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


def test_nav_click_creates_tab(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state)

    # Dashboard tab already open from __init__; click on Goals (y=11)
    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=1.0)
    tui._ingest_mouse_event(x=2, y=11, event_type="up", buttons=0, now=1.1)

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
    tui._ingest_mouse_event(x=2, y=11, event_type="up", buttons=0, now=1.1)
    count_before = len(tui.state.open_tabs)
    tui._ingest_mouse_event(x=2, y=11, event_type="down", buttons=1, now=2.0)
    tui._ingest_mouse_event(x=2, y=11, event_type="up", buttons=0, now=2.1)
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
    tui._ingest_mouse_event(x=2, y=11, event_type="up", buttons=0, now=1.1)
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
    tui._ingest_mouse_event(x=2, y=11, event_type="up", buttons=0, now=1.1)
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


# ── Templates Section ────────────────────────────────────────────────────────


def test_mouse_drag_selection_works_in_middle_content_without_snake_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(endpoint="http://localhost:5000", section_id="dashboard", focus=FocusPane.CONTENT)
    tui = InteractiveOperatorTui(
        state,
        registry=SectionAdapterRegistry(
            loader=lambda section_id: SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")
        ),
    )

    tui._ingest_mouse_event(x=28, y=12, event_type="down", buttons=1, now=1.0)
    tui._ingest_mouse_event(x=36, y=14, event_type="move", buttons=1, now=1.1)
    tui._ingest_mouse_event(x=36, y=14, event_type="up", buttons=1, now=1.2)

    game = dict(tui.state.header_logo_game or {})
    cells = {
        (int(cell[0]), int(cell[1]))
        for cell in (game.get("selection_cells") or [])
        if isinstance(cell, (list, tuple)) and len(cell) == 2
    }
    assert (28, 12) in cells
    assert (36, 14) in cells
    assert (30, 13) in cells
    assert tuple(game.get("selection_regions") or [None])[0] == (28, 12, 36, 14)


