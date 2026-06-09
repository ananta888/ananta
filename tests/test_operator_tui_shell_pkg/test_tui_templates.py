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


def test_templates_section_in_nav() -> None:
    from client_surfaces.operator_tui.sections import SECTIONS, normalize_section_id
    ids = [s.id for s in SECTIONS]
    assert "templates" in ids
    assert normalize_section_id("tpl") == "templates"
    assert normalize_section_id("blueprint") == "templates"



def test_templates_content_renders_groups() -> None:
    from client_surfaces.operator_tui.renderer import _templates_content_lines
    payload = {
        "items": [
            {"id": "bp:1", "kind": "blueprint", "title": "prod-team", "description": "",
             "roles_count": 3, "artifacts_count": 1, "is_seed": True, "base_team_type": "standard", "raw_id": "1"},
            {"id": "tpl:a", "kind": "template", "title": "agent_sys",
             "prompt_preview": "Du bist ein Agent.", "raw_id": "a"},
        ],
        "blueprints_count": 1, "templates_count": 1,
        "blueprints_raw": [], "templates_raw": [],
    }
    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", selected_index=0)
    lines = _templates_content_lines(payload, state, 80)
    joined = "\n".join(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", l) for l in lines)
    assert "1 blueprints" in joined
    assert "prod-team" in joined
    assert "agent_sys" in joined
    assert "Tree:" in joined
    assert "├─ Blueprints" in joined
    assert "└─ Prompt-Templates" in joined
    assert "Blueprints" in joined
    assert "Prompt-Templates" in joined



def test_templates_content_selected_marker() -> None:
    from client_surfaces.operator_tui.renderer import _templates_content_lines
    payload = {
        "items": [
            {"id": "bp:1", "kind": "blueprint", "title": "my-bp", "description": "",
             "roles_count": 1, "artifacts_count": 0, "is_seed": False, "base_team_type": "", "raw_id": "1"},
            {"id": "tpl:a", "kind": "template", "title": "my-tpl", "prompt_preview": "", "raw_id": "a"},
        ],
        "blueprints_count": 1, "templates_count": 1,
        "blueprints_raw": [], "templates_raw": [],
    }
    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", selected_index=0)
    lines = _templates_content_lines(payload, state, 80)
    bp_line = next((l for l in lines if "my-bp" in re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", l)), "")
    assert ">" in bp_line[:4]



def test_templates_inspect_blueprint() -> None:
    from client_surfaces.operator_tui.read_models import build_templates_inspect
    payload = {
        "items": [
            {"id": "bp:1", "kind": "blueprint", "title": "test-bp", "description": "Beschreibung",
             "roles_count": 1, "is_seed": False, "base_team_type": "", "raw_id": "bp-1"},
        ],
        "blueprints_raw": [{
            "id": "bp-1", "name": "test-bp", "description": "Beschreibung", "is_seed": False,
            "base_team_type_name": "",
            "roles": [{"name": "agent", "is_required": True, "template_id": "tpl-x"}],
            "artifacts": [],
        }],
        "templates_raw": [],
    }
    detail = build_templates_inspect(payload, 0)
    joined = "\n".join(detail)
    assert "test-bp" in joined
    assert "agent" in joined
    assert "tpl:tpl-x" in joined



def test_templates_inspect_template() -> None:
    from client_surfaces.operator_tui.read_models import build_templates_inspect
    payload = {
        "items": [
            {"id": "tpl:a", "kind": "template", "title": "worker_v2",
             "description": "Worker-Prompt", "prompt_preview": "Du bearbeitest...", "raw_id": "a"},
        ],
        "blueprints_raw": [],
        "templates_raw": [{
            "id": "a", "name": "worker_v2", "description": "Worker-Prompt",
            "prompt_template": "Du bearbeitest die Aufgabe: {{ task }}",
        }],
    }
    detail = build_templates_inspect(payload, 0)
    joined = "\n".join(detail)
    assert "worker_v2" in joined
    assert "{{ task }}" in joined



def test_templates_enter_opens_middle_editor() -> None:
    payload = {
        "items": [
            {"id": "tpl:a", "kind": "template", "title": "worker_v2", "prompt_preview": "Du bearbeitest...", "raw_id": "a"},
        ],
        "blueprints_count": 0,
        "templates_count": 1,
        "system_prompts_count": 0,
        "blueprints_raw": [],
        "templates_raw": [
            {
                "id": "a",
                "name": "worker_v2",
                "description": "Worker-Prompt",
                "prompt_template": "Du bearbeitest die Aufgabe: {{ task }}",
            }
        ],
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "templates":
            return SectionLoadResult(
                section_id="templates",
                state=PanelState.HEALTHY,
                payload=payload,
                message="loaded templates",
            )
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", focus=FocusPane.CONTENT, selected_index=0)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))

    assert tui._open_audit_viewer_for_selected() is True
    output = render_operator_shell(tui.state, width=110, height=36)

    assert tui.state.mode is OperatorMode.EDIT
    assert "template editor" in tui.state.status_message
    assert "Template Editor" in output
    assert "{{ task }}" in output



def test_templates_enter_opens_blueprint_editor() -> None:
    payload = {
        "items": [
            {"id": "bp:1", "kind": "blueprint", "title": "ops-team", "description": "Ops Team", "raw_id": "bp-1"},
        ],
        "blueprints_count": 1,
        "templates_count": 0,
        "system_prompts_count": 0,
        "blueprints_raw": [
            {
                "id": "bp-1",
                "name": "ops-team",
                "description": "Ops Team",
                "roles": [{"name": "operator", "template_id": "tpl-op", "sort_order": 0, "is_required": True, "config": {}}],
                "artifacts": [],
            }
        ],
        "templates_raw": [],
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "templates":
            return SectionLoadResult(section_id="templates", state=PanelState.HEALTHY, payload=payload, message="loaded templates")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", focus=FocusPane.CONTENT, selected_index=0)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))

    tui._handle_enter_key()
    output = render_operator_shell(tui.state, width=110, height=36)
    editor = dict((tui.state.header_logo_game or {}).get("template_editor") or {})

    assert tui.state.mode is OperatorMode.EDIT
    assert "template editor" in tui.state.status_message
    assert "Template Editor" in output
    assert "\"name\": \"ops-team\"" in str(editor.get("text") or "")



def test_templates_navigation_expands_tree_under_templates() -> None:
    from client_surfaces.operator_tui.renderer import _navigation_lines

    payload = {
        "items": [
            {"id": "tpl:a", "kind": "template", "title": "worker_v2", "raw_id": "a"},
            {"id": "sys:b", "kind": "system_prompt", "title": "agent_sys", "raw_id": "b"},
        ],
        "templates_raw": [],
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="templates",
        focus=FocusPane.NAVIGATION,
        selected_index=len(SECTIONS),
        section_payloads={"templates": payload},
    )

    lines = _navigation_lines(state)
    joined = "\n".join(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line) for line in lines)
    assert "Templates" in joined
    assert "Prompt-Templates (1)" in joined
    assert "System-Prompts (1)" in joined
    assert "worker_v2" in joined
    assert "agent_sys" in joined



def test_templates_navigation_item_enter_opens_editor_in_middle() -> None:
    payload = {
        "items": [
            {"id": "tpl:a", "kind": "template", "title": "worker_v2", "prompt_preview": "Du bearbeitest...", "raw_id": "a"},
        ],
        "blueprints_count": 0,
        "templates_count": 1,
        "system_prompts_count": 0,
        "blueprints_raw": [],
        "templates_raw": [
            {"id": "a", "name": "worker_v2", "description": "Worker-Prompt", "prompt_template": "Du bearbeitest die Aufgabe: {{ task }}"}
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
        selected_index=len(SECTIONS),
    )
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))

    tui._handle_enter_key()
    output = render_operator_shell(tui.state, width=110, height=36)

    assert tui.state.focus is FocusPane.CONTENT
    assert tui.state.mode is OperatorMode.EDIT
    assert "Template Editor" in output
    assert "{{ task }}" in output



def test_templates_navigation_blueprint_item_enter_opens_editor_in_middle() -> None:
    payload = {
        "items": [
            {"id": "bp:1", "kind": "blueprint", "title": "ops-team", "description": "Ops Team", "raw_id": "bp-1"},
        ],
        "blueprints_count": 1,
        "templates_count": 0,
        "system_prompts_count": 0,
        "blueprints_raw": [
            {"id": "bp-1", "name": "ops-team", "description": "Ops Team", "roles": [], "artifacts": []}
        ],
        "templates_raw": [],
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "templates":
            return SectionLoadResult(section_id="templates", state=PanelState.HEALTHY, payload=payload, message="loaded templates")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", focus=FocusPane.NAVIGATION, selected_index=len(SECTIONS))
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))

    tui._handle_enter_key()
    output = render_operator_shell(tui.state, width=110, height=36)
    editor = dict((tui.state.header_logo_game or {}).get("template_editor") or {})

    assert tui.state.focus is FocusPane.CONTENT
    assert tui.state.mode is OperatorMode.EDIT
    assert "Template Editor" in output
    assert "\"name\": \"ops-team\"" in str(editor.get("text") or "")



def test_template_editor_resets_when_leaving_templates_section() -> None:
    templates_payload = {
        "items": [
            {"id": "bp:1", "kind": "blueprint", "title": "ops-team", "description": "Ops Team", "raw_id": "bp-1"},
        ],
        "blueprints_count": 1,
        "templates_count": 0,
        "system_prompts_count": 0,
        "blueprints_raw": [{"id": "bp-1", "name": "ops-team", "description": "Ops Team", "roles": [], "artifacts": []}],
        "templates_raw": [],
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "templates":
            return SectionLoadResult(section_id="templates", state=PanelState.HEALTHY, payload=templates_payload, message="loaded templates")
        if section_id == "goals":
            return SectionLoadResult(section_id="goals", state=PanelState.EMPTY, payload={"items": []}, message="empty goals")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", focus=FocusPane.CONTENT, selected_index=0)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))

    tui._handle_enter_key()
    assert tui.state.mode is OperatorMode.EDIT
    assert bool(dict((tui.state.header_logo_game or {}).get("template_editor") or {}).get("active"))

    tui._run_command(":section goals")
    assert tui.state.section_id == "goals"
    assert tui.state.mode is OperatorMode.NORMAL
    assert not bool(dict((tui.state.header_logo_game or {}).get("template_editor") or {}).get("active"))

    tui._run_command(":section templates")
    output = render_operator_shell(tui.state, width=110, height=36)
    assert tui.state.section_id == "templates"
    assert tui.state.mode is OperatorMode.NORMAL
    assert "Tree:" in output
    assert "Template Editor" not in output



def test_template_editor_left_right_updates_horizontal_view_offset(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.interactive.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((100, 33)),
    )
    long_prompt = "X" * 200
    payload = {
        "items": [{"id": "tpl:a", "kind": "template", "title": "wide", "prompt_preview": "wide", "raw_id": "a"}],
        "templates_raw": [{"id": "a", "name": "wide", "prompt_template": long_prompt}],
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "templates":
            return SectionLoadResult(section_id="templates", state=PanelState.HEALTHY, payload=payload, message="loaded templates")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", focus=FocusPane.CONTENT, selected_index=0)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))
    tui._handle_enter_key()
    editor = dict((tui.state.header_logo_game or {}).get("template_editor") or {})
    start_offset = int(editor.get("view_col_offset") or 0)
    assert start_offset > 0

    tui._template_editor_move_cursor(-80)
    editor_after_left = dict((tui.state.header_logo_game or {}).get("template_editor") or {})
    left_offset = int(editor_after_left.get("view_col_offset") or 0)
    assert left_offset < start_offset

    tui._template_editor_move_cursor(80)
    editor_after_right = dict((tui.state.header_logo_game or {}).get("template_editor") or {})
    right_offset = int(editor_after_right.get("view_col_offset") or 0)
    assert right_offset >= left_offset



def test_template_editor_mouse_wheel_scrolls_vertical_in_middle(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    monkeypatch.setattr(
        "client_surfaces.operator_tui.interactive.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    multi = "\n".join(f"line {i:03d}" for i in range(80))
    payload = {
        "items": [{"id": "tpl:a", "kind": "template", "title": "scroll", "prompt_preview": "scroll", "raw_id": "a"}],
        "templates_raw": [{"id": "a", "name": "scroll", "prompt_template": multi}],
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "templates":
            return SectionLoadResult(section_id="templates", state=PanelState.HEALTHY, payload=payload, message="loaded templates")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", focus=FocusPane.CONTENT, selected_index=0)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))
    tui._handle_enter_key()

    # Middle pane starts around x=24, y=8 in current layout.
    tui._ingest_mouse_event(x=24, y=12, event_type="scroll_down", scroll_delta=1, now=1.0)
    editor_down = dict((tui.state.header_logo_game or {}).get("template_editor") or {})
    down_offset = int(editor_down.get("view_line_offset") or 0)
    assert down_offset > 0

    tui._ingest_mouse_event(x=24, y=12, event_type="scroll_up", scroll_delta=-1, now=2.0)
    editor_up = dict((tui.state.header_logo_game or {}).get("template_editor") or {})
    up_offset = int(editor_up.get("view_line_offset") or 0)
    assert up_offset < down_offset



def test_template_editor_click_sets_cursor_position(monkeypatch) -> None:
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    monkeypatch.setattr(
        "client_surfaces.operator_tui.interactive.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    payload = {
        "items": [{"id": "tpl:a", "kind": "template", "title": "click", "prompt_preview": "click", "raw_id": "a"}],
        "templates_raw": [{"id": "a", "name": "click", "prompt_template": "aaaaa\nbbbbbbbbbb\ncc"}],
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "templates":
            return SectionLoadResult(section_id="templates", state=PanelState.HEALTHY, payload=payload, message="loaded templates")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", focus=FocusPane.CONTENT, selected_index=0)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))
    tui._handle_enter_key()

    # content_x1=24, body_y1=9 => 2nd text row is y=14, text starts after 6 prefix chars.
    click_x = 24 + 6 + 3
    click_y = 14
    tui._ingest_mouse_event(x=click_x, y=click_y, event_type="down", buttons=1, now=1.0)

    editor = dict((tui.state.header_logo_game or {}).get("template_editor") or {})
    # "aaaaa\\n" (6 chars) + 3 chars into second line
    assert int(editor.get("cursor") or -1) == 9



def test_template_editor_highlights_template_variables(monkeypatch) -> None:
    payload = {
        "items": [{"id": "tpl:a", "kind": "template", "title": "vars", "prompt_preview": "vars", "raw_id": "a"}],
        "templates_raw": [{"id": "a", "name": "vars", "prompt_template": "Hello {{ task }} and {{worker_id}}"}],
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "templates":
            return SectionLoadResult(section_id="templates", state=PanelState.HEALTHY, payload=payload, message="loaded templates")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", focus=FocusPane.CONTENT, selected_index=0)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))
    tui._handle_enter_key()
    output = render_operator_shell(tui.state, width=110, height=36)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)

    assert "\x1b[38;2;130;210;255m{{ task }}\x1b[0m" in output
    assert "\x1b[38;2;130;210;255m{{worker_id}}\x1b[0m" in output
    assert "Lint: ok" in plain



def test_template_editor_marks_lint_problems(monkeypatch) -> None:
    payload = {
        "items": [{"id": "tpl:a", "kind": "template", "title": "lint", "prompt_preview": "lint", "raw_id": "a"}],
        "templates_raw": [{"id": "a", "name": "lint", "prompt_template": "valid {{ task }}\ninvalid {{ broken"}],
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "templates":
            return SectionLoadResult(section_id="templates", state=PanelState.HEALTHY, payload=payload, message="loaded templates")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="templates", focus=FocusPane.CONTENT, selected_index=0)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))
    tui._handle_enter_key()
    output = render_operator_shell(tui.state, width=110, height=36)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)

    assert "Lint: 1" in plain
    assert "\x1b[38;2;255;120;120m" in output
