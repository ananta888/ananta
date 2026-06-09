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


def test_audit_navigation_expands_tree_under_audit() -> None:
    from client_surfaces.operator_tui.renderer import _navigation_lines

    payload = {
        "items": [
            {"id": "audit.logs.recent", "group": "Audit Logs", "title": "Recent Logs", "status": "ok"},
            {"id": "runtime.stats.snapshot", "group": "Runtime Telemetry", "title": "Stats Snapshot", "status": "ok"},
        ],
        "datasets": {
            "audit.logs.recent": [{"id": "x"}],
            "runtime.stats.snapshot": {"uptime": 1},
        },
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="audit",
        focus=FocusPane.NAVIGATION,
        selected_index=len(SECTIONS),
        section_payloads={"audit": payload},
    )

    lines = _navigation_lines(state)
    joined = "\n".join(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line) for line in lines)
    assert "Audit" in joined
    assert "Audit Logs (1)" in joined
    assert "Runtime Telemetry (1)" in joined
    assert "Recent Logs" in joined
    assert "Stats Snapshot" in joined



def test_audit_navigation_includes_data_cleanup_group() -> None:
    from client_surfaces.operator_tui.audit_cleanup import build_audit_cleanup_entries
    from client_surfaces.operator_tui.renderer import _navigation_lines

    cleanup_items, cleanup_datasets = build_audit_cleanup_entries()
    payload = {
        "items": list(cleanup_items),
        "datasets": dict(cleanup_datasets),
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="audit",
        focus=FocusPane.NAVIGATION,
        selected_index=len(SECTIONS),
        section_payloads={"audit": payload},
    )

    lines = _navigation_lines(state)
    joined = "\n".join(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line) for line in lines)

    assert "Data Cleanup" in joined
    assert "Nur Audit loeschen" in joined
    assert "Alles loeschen" in joined



def test_audit_navigation_item_enter_opens_read_only_viewer() -> None:
    payload = {
        "items": [
            {"id": "audit.logs.recent", "dataset_id": "audit.logs.recent", "group": "Audit Logs", "title": "Recent Logs", "status": "ok"},
        ],
        "datasets": {
            "audit.logs.recent": [{"id": "evt-1", "kind": "chat_message"}],
        },
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "audit":
            return SectionLoadResult(section_id="audit", state=PanelState.HEALTHY, payload=payload, message="loaded audit")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="audit", focus=FocusPane.NAVIGATION, selected_index=len(SECTIONS))
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))

    tui._handle_enter_key()
    output = render_operator_shell(tui.state, width=110, height=36)
    viewer = dict((tui.state.header_logo_game or {}).get("audit_viewer") or {})

    assert tui.state.focus is FocusPane.CONTENT
    assert tui.state.mode is OperatorMode.NORMAL
    assert bool(viewer.get("active"))
    assert "Audit Viewer" in output
    assert "chat_message" in output



def test_audit_cleanup_item_opens_confirmation_view() -> None:
    payload = {
        "items": [
            {
                "id": "audit.cleanup.audit_only",
                "dataset_id": "audit.cleanup.audit_only",
                "group": "Data Cleanup",
                "title": "Nur Audit loeschen",
                "status": "ok",
            },
        ],
        "datasets": {
            "audit.cleanup.audit_only": {
                "kind": "cleanup_action",
                "cleanup_action_id": "audit_only",
                "details": ["Loescht Audit-Daten."],
            },
        },
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "audit":
            return SectionLoadResult(section_id="audit", state=PanelState.HEALTHY, payload=payload, message="loaded audit")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="audit", focus=FocusPane.CONTENT, selected_index=0)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))
    tui._set_state(
        tui.state.with_updates(
            section_id="audit",
            focus=FocusPane.CONTENT,
            selected_index=0,
            section_payloads={"audit": payload},
            panel_states={"audit": PanelState.HEALTHY},
        )
    )
    assert tui._open_audit_viewer_for_selected() is True
    output = render_operator_shell(tui.state, width=110, height=36)
    viewer = dict((tui.state.header_logo_game or {}).get("audit_viewer") or {})

    assert str(viewer.get("mode") or "") == "confirm_cleanup"
    assert "Audit Cleanup" in output
    assert "Links/Rechts waehlt Button" in output
    assert "[ Loeschen ]" in output
    assert "[ Abbrechen ]" in output



def test_audit_cleanup_confirmation_enter_executes_action(monkeypatch) -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="audit",
        focus=FocusPane.CONTENT,
        header_logo_game={
            "audit_viewer": {
                "active": True,
                "mode": "confirm_cleanup",
                "cleanup_action_id": "audit_only",
                "confirm_choice": "delete",
            },
        },
    )
    tui = InteractiveOperatorTui(state)

    monkeypatch.setattr(
        "client_surfaces.operator_tui.interactive.run_audit_cleanup_action",
        lambda action_id: {"action_id": action_id, "counts": {"audit_db_rows": 5}},
    )

    tui._handle_enter_key()

    game = dict(tui.state.header_logo_game or {})
    viewer = dict(game.get("audit_viewer") or {})
    assert bool(viewer.get("active"))
    assert viewer.get("mode") == "cleanup_result"
    assert "cleanup ausgefuehrt: audit_only" in tui.state.status_message
    assert "audit_db_rows=5" in tui.state.status_message



def test_audit_cleanup_confirmation_enter_on_cancel_aborts(monkeypatch) -> None:
    called: list[str] = []
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="audit",
        focus=FocusPane.CONTENT,
        header_logo_game={
            "audit_viewer": {
                "active": True,
                "mode": "confirm_cleanup",
                "cleanup_action_id": "audit_only",
                "confirm_choice": "cancel",
            },
        },
    )
    tui = InteractiveOperatorTui(state)

    def _fake_run(action_id: str):
        called.append(action_id)
        return {"action_id": action_id, "counts": {"audit_db_rows": 1}}

    monkeypatch.setattr("client_surfaces.operator_tui.interactive.run_audit_cleanup_action", _fake_run)

    tui._handle_enter_key()

    game = dict(tui.state.header_logo_game or {})
    viewer = dict(game.get("audit_viewer") or {})
    assert bool(viewer.get("active"))
    assert viewer.get("mode") == "cleanup_result"
    assert called == []
    assert tui.state.status_message == "cleanup abgebrochen"



def test_audit_cleanup_confirmation_left_right_switches_button_choice() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="audit",
        focus=FocusPane.CONTENT,
        header_logo_game={
            "audit_viewer": {
                "active": True,
                "mode": "confirm_cleanup",
                "cleanup_action_id": "audit_only",
                "confirm_choice": "cancel",
            },
        },
    )
    tui = InteractiveOperatorTui(state)

    tui._audit_cleanup_set_choice("delete")
    viewer = dict((tui.state.header_logo_game or {}).get("audit_viewer") or {})
    assert viewer.get("confirm_choice") == "delete"

    tui._audit_cleanup_set_choice("cancel")
    viewer = dict((tui.state.header_logo_game or {}).get("audit_viewer") or {})
    assert viewer.get("confirm_choice") == "cancel"



def test_audit_cleanup_confirm_result_enter_closes_viewer() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="audit",
        focus=FocusPane.CONTENT,
        header_logo_game={
            "audit_viewer": {
                "active": True,
                "mode": "cleanup_result",
                "title": "Nur Audit loeschen",
                "text": "cleanup ausgefuehrt",
            },
        },
    )
    tui = InteractiveOperatorTui(state)

    tui._handle_enter_key()

    viewer = dict((tui.state.header_logo_game or {}).get("audit_viewer") or {})
    assert not bool(viewer.get("active"))
    assert tui.state.status_message == "cleanup viewer geschlossen"



def test_audit_cleanup_result_is_visible_in_middle_panel() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="audit",
        focus=FocusPane.CONTENT,
        header_logo_game={
            "audit_viewer": {
                "active": True,
                "mode": "cleanup_result",
                "title": "Nur Audit loeschen",
                "group": "Data Cleanup",
                "text": "cleanup ausgefuehrt: audit_only (audit_db_rows=2)",
            },
        },
    )
    tui = InteractiveOperatorTui(state)

    output = render_operator_shell(tui.state, width=120, height=36)

    assert "Audit Cleanup Ergebnis" in output
    assert "cleanup ausgefuehrt: audit_only" in output



def test_audit_cleanup_mouse_click_delete_executes(monkeypatch) -> None:
    called: list[str] = []
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="audit",
        focus=FocusPane.CONTENT,
        header_logo_game={
            "audit_viewer": {
                "active": True,
                "mode": "confirm_cleanup",
                "title": "Nur Audit loeschen",
                "cleanup_action_id": "audit_only",
                "confirm_choice": "cancel",
                "text": "Bitte Loeschung bestaetigen.\n\nA\nB",
                "view_line_offset": 0,
            },
        },
    )
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(
        "client_surfaces.operator_tui.interactive.run_audit_cleanup_action",
        lambda action_id: called.append(action_id) or {"action_id": action_id, "counts": {"audit_db_rows": 1}},
    )

    button_row = None
    for y in range(0, 40):
        if tui._audit_cleanup_button_choice_from_click(x=28, y=y, width=120, height=31) == "delete":
            button_row = y
            break
    assert button_row is not None

    handled = tui._audit_cleanup_handle_mouse_click(x=28, y=int(button_row), width=120, height=31)

    viewer = dict((tui.state.header_logo_game or {}).get("audit_viewer") or {})
    assert handled is True
    assert called == ["audit_only"]
    assert viewer.get("mode") == "cleanup_result"



def test_audit_cleanup_mouse_click_cancel_aborts(monkeypatch) -> None:
    called: list[str] = []
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="audit",
        focus=FocusPane.CONTENT,
        header_logo_game={
            "audit_viewer": {
                "active": True,
                "mode": "confirm_cleanup",
                "title": "Nur Audit loeschen",
                "cleanup_action_id": "audit_only",
                "confirm_choice": "cancel",
                "text": "Bitte Loeschung bestaetigen.\n\nA\nB",
                "view_line_offset": 0,
            },
        },
    )
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(
        "client_surfaces.operator_tui.interactive.run_audit_cleanup_action",
        lambda action_id: called.append(action_id) or {"action_id": action_id, "counts": {"audit_db_rows": 1}},
    )

    button_row = None
    for y in range(0, 40):
        if tui._audit_cleanup_button_choice_from_click(x=70, y=y, width=120, height=31) == "cancel":
            button_row = y
            break
    assert button_row is not None

    handled = tui._audit_cleanup_handle_mouse_click(x=70, y=int(button_row), width=120, height=31)

    viewer = dict((tui.state.header_logo_game or {}).get("audit_viewer") or {})
    assert handled is True
    assert called == []
    assert viewer.get("mode") == "cleanup_result"
    assert tui.state.status_message == "cleanup abgebrochen"



def test_audit_viewer_resets_when_leaving_audit_section() -> None:
    audit_payload = {
        "items": [
            {"id": "audit.logs.recent", "dataset_id": "audit.logs.recent", "group": "Audit Logs", "title": "Recent Logs", "status": "ok"},
        ],
        "datasets": {"audit.logs.recent": [{"id": "evt-1"}]},
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "audit":
            return SectionLoadResult(section_id="audit", state=PanelState.HEALTHY, payload=audit_payload, message="loaded audit")
        if section_id == "goals":
            return SectionLoadResult(section_id="goals", state=PanelState.EMPTY, payload={"items": []}, message="empty goals")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="audit", focus=FocusPane.CONTENT, selected_index=0)
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))

    tui._handle_enter_key()
    assert bool(dict((tui.state.header_logo_game or {}).get("audit_viewer") or {}).get("active"))

    tui._run_command(":section goals")
    assert tui.state.section_id == "goals"
    assert not bool(dict((tui.state.header_logo_game or {}).get("audit_viewer") or {}).get("active"))

    tui._run_command(":section audit")
    output = render_operator_shell(tui.state, width=110, height=36)
    assert tui.state.section_id == "audit"
    assert "Audit Viewer" not in output



def test_audit_chat_prompt_item_shows_final_prompt_in_viewer() -> None:
    payload = {
        "items": [
            {
                "id": "llm.requests.chat_prompt.trace-1",
                "dataset_id": "llm.requests.chat_prompt.trace-1",
                "group": "LLM/Debug",
                "title": "Chat Prompt #1 · trace-1",
                "status": "ok",
            },
        ],
        "datasets": {
            "llm.requests.chat_prompt.trace-1": {
                "trace_id": "trace-1",
                "final_prompt_redacted": "SYSTEM: context for ananta\nUSER: hi",
                "detail": {"messages_redacted": [{"role": "user", "content": "hi"}]},
            }
        },
    }

    def _loader(section_id: str) -> SectionLoadResult:
        if section_id == "audit":
            return SectionLoadResult(section_id="audit", state=PanelState.HEALTHY, payload=payload, message="loaded audit")
        return SectionLoadResult(section_id=section_id, state=PanelState.EMPTY, payload={}, message="empty")

    state = OperatorState(endpoint="http://localhost:5000", section_id="audit", focus=FocusPane.NAVIGATION, selected_index=len(SECTIONS))
    tui = InteractiveOperatorTui(state, registry=SectionAdapterRegistry(loader=_loader))

    tui._handle_enter_key()
    output = render_operator_shell(tui.state, width=120, height=36)

    assert "Audit Viewer" in output
    assert "SYSTEM: context for ananta" in output
    assert "USER: hi" in output


