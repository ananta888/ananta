from __future__ import annotations

import json
import hashlib
import urllib.error
import urllib.request
import urllib.parse
import os
import shutil
import html as _html
from datetime import UTC, datetime
from pathlib import Path

from agent.artifacts.artifact_access_policy import ArtifactAccessPolicy
from agent.artifacts.artifact_candidate_service import ArtifactCandidateService
from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
from agent.repository import goal_repo, task_repo
from agent.services.planning_summary_doctor_service import doctor_file, fix_file
from agent.services.planning_summary_engine import PlanningSummaryEngine
from agent.sources.citation_formatter import format_citation
from agent.sources.builtin_sources import load_builtin_source_descriptors
from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_pack_service import SourcePackService
from agent.sources.source_snapshot_store import SourceSnapshotStore
from client_surfaces.operator_tui.actions import dispatch_action, parse_action
from client_surfaces.operator_tui.ai_snake_learning import apply_prediction_feedback, event_for_prediction_feedback
from client_surfaces.operator_tui.browser import browser_fallback_url
from client_surfaces.operator_tui.ai_snake_context import get_ai_context
from client_surfaces.operator_tui.ai_snake_config_view import chat_model_option_label, refresh_chat_backend_models
from client_surfaces.operator_tui.snake_persistence import save_tui_chat_settings
from client_surfaces.operator_tui.keybindings_config import display_for_action
from client_surfaces.operator_tui.goal_artifact_filters import (
    filter_goal_artifact_view,
    normalize_goal_artifact_filters,
)
from client_surfaces.operator_tui.ai_snake_context import explain_goal_artifact_graph
from client_surfaces.operator_tui.ai_snake_training_import_export import (
    export_training_bundle_to_path,
    export_training_markdown,
    import_training_bundle,
)
from client_surfaces.operator_tui import chat_state as chat_state_utils
from client_surfaces.operator_tui.ai_snake_training_store import (
    append_behavior_event,
    build_training_bundle,
    compact_training_data,
    data_path_status,
    data_show_status,
    delete_events,
    delete_patterns,
    pattern_detail,
    patterns_status_lines,
    read_active_profile,
    read_patterns,
    reset_training_data,
    save_patterns,
    save_active_profile,
)
from client_surfaces.operator_tui.models import CommandResult, FocusPane, OperatorMode, OperatorState, PanelState
from client_surfaces.operator_tui.sections import move_section, normalize_section_id, section_ids
from client_surfaces.operator_tui.diff.ai_diff_dispatch import dispatch_ai_diff_request
from client_surfaces.operator_tui.diff.ai_diff_panel_state import build_ai_diff_panel_state, set_ai_diff_mode
from client_surfaces.operator_tui.diff.diff_engine import DiffEngine
from client_surfaces.operator_tui.diff.diff_source_resolver import DiffSourceResolver
from client_surfaces.operator_tui.diff.diff_sources import build_current_diff_source_ref, build_output_artifact_source_ref
from client_surfaces.operator_tui.diff.three_way_diff_state import (
    build_current_diff_three_panel_session,
    set_panel_state,
    validate_three_way_diff_session,
)
from agent.services.planning_track_pipeline_service import persist_planning_track_result
from agent.services.planning_track_planner_service import build_planner_context_envelope, render_track_planning_prompt
from agent.services.planning_track_task_integration_service import PlanningTrackTaskIntegrationService
from agent.services.helpcenter_contract_service import load_helpcenter_index
from agent.services.helpcenter_ingest_service import ingest_github_failures, StaticGithubWorkflowApiClient
from client_surfaces.operator_tui.commands_share import _handle_share_command
from client_surfaces.operator_tui.commands_oidc import _handle_oidc_command
from client_surfaces.operator_tui.commands_webrtc import execute_center_browser_command, _handle_webrtc_command
from client_surfaces.operator_tui.commands_mail import handle_mail_command
from client_surfaces.operator_tui.commands_helpcenter import handle_helpcenter_command
from client_surfaces.operator_tui.commands_planning import handle_diff3_command, handle_plan_command
from client_surfaces.operator_tui.commands_visual import handle_visual_commands
from client_surfaces.operator_tui.commands_sources import handle_sources_command
from client_surfaces.operator_tui.commands_goal import handle_goal_command, handle_artifact_command
from client_surfaces.operator_tui.commands_ai import handle_ai_command, _resolve_chat_ask_timeout_seconds
from client_surfaces.operator_tui.commands_rag import handle_rag_command, handle_te_command, handle_sim_command, handle_tutorial_command, handle_tutorials_command, handle_snakes_command, handle_msg_command
from client_surfaces.operator_tui.commands_chat import handle_chat_command, handle_notes_command, handle_channels_command, handle_ai_context_command
from client_surfaces.operator_tui.commands_run_control import handle_run_command, handle_approval_command, handle_branch_command

def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")





def execute_command(raw_command: str, state: OperatorState) -> CommandResult:
    # Dispatch center.browser.* commands first (carbonyl-005)
    browser_result = execute_center_browser_command(raw_command, state)
    if browser_result is not None:
        return browser_result

    text = str(raw_command or "").strip()
    while text.startswith(":") or text.startswith("/"):
        text = text[1:].strip()
    if not text:
        return CommandResult(state.with_updates(mode=OperatorMode.NORMAL, command_line=""), "empty command ignored")

    parts = text.split()
    command = parts[0].lower()
    args = parts[1:]

    if command in {"refresh", "r"}:
        return CommandResult(
            state.with_updates(
                mode=OperatorMode.NORMAL,
                command_line="",
                refresh_count=state.refresh_count + 1,
                status_message="refresh requested",
            ),
            "refresh requested",
        )
    if command in {"section", "open", "goto"}:
        if not args:
            return CommandResult(state.with_updates(mode=OperatorMode.COMMAND), "section command requires a section id")
        section_id = normalize_section_id(args[0])
        return CommandResult(
            state.with_updates(
                mode=OperatorMode.NORMAL,
                command_line="",
                section_id=section_id,
                selected_index=0,
                status_message=f"section {section_id}",
            ),
            f"opened section {section_id}",
        )
    if command == "next":
        section_id = move_section(state.section_id, 1)
        return CommandResult(state.with_updates(section_id=section_id, selected_index=0), f"opened section {section_id}")
    if command == "prev":
        section_id = move_section(state.section_id, -1)
        return CommandResult(state.with_updates(section_id=section_id, selected_index=0), f"opened section {section_id}")
    if command == "focus":
        if not args:
            return CommandResult(state, "focus command requires navigation, content, or detail")
        requested = args[0].lower()
        try:
            focus = FocusPane(requested)
        except ValueError:
            return CommandResult(state, f"unknown focus pane: {requested}", handled=False)
        return CommandResult(state.with_updates(focus=focus, status_message=f"focus {focus.value}"), f"focus {focus.value}")
    if command == "mode":
        if not args:
            return CommandResult(state, "mode command requires normal, command, inspect, or edit")
        requested = args[0].lower()
        try:
            mode = OperatorMode(requested)
        except ValueError:
            return CommandResult(state, f"unknown mode: {requested}", handled=False)
        return CommandResult(state.with_updates(mode=mode, status_message=f"mode {mode.value}"), f"mode {mode.value}")
    if command in {"help", "?"}:
        if args:
            sub = args[0].lower()
            if sub == "chat":
                msg = "chat: [c] focus | Esc game | :chat room|ai|@id|retry | :chat backend list|use|status | :chat model list|use"
                return CommandResult(state.with_updates(status_message=msg), "help chat")
            if sub == "notes":
                msg = "notes: :notes | :notes find <t> | :notes pin/unpin/delete <id> | LOCAL ONLY"
                return CommandResult(state.with_updates(status_message=msg), "help notes")
            if sub == "rag":
                msg = "rag: :rag why <frage> — zeigt Retrieval-Trace ohne LLM | :rag why --json <frage> — JSON-Output"
                return CommandResult(state.with_updates(status_message=msg), "help rag")
            if sub == "te":
                msg = "te: :te status — Task-Engine-Status | :te classify <kind> — Klassifizierung testen"
                return CommandResult(state.with_updates(status_message=msg), "help te")
            if sub == "sim":
                msg = "sim: :sim list — Szenarien | :sim run <name> [--ticks N] — Simulation starten"
                return CommandResult(state.with_updates(status_message=msg), "help sim")
        return CommandResult(state.with_updates(show_help=not state.show_help, status_message="help toggled"), "help toggled")
    if command in {"config", "cfg", "ai-config", "snake-config"}:
        game = dict(state.header_logo_game or {})
        opened = not bool(game.get("ai_snake_config_open"))
        game["ai_snake_config_open"] = opened
        if opened:
            game["artifact_chat_focus"] = False
            from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
            chat = get_chat_state(game)
            chat["chat_focus"] = False
            set_chat_state(game, chat)
            game["ai_snake_config_combo"] = {
                "open": False,
                "key": "",
                "filter": "",
                "filter_cursor": 0,
                "selected_option": 0,
            }
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    focus=FocusPane.CONTENT,
                    selected_index=0,
                    status_message="ai config: offen",
                ),
                "ai config opened",
            )
        game["ai_snake_config_combo"] = {"open": False}
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message="ai config: geschlossen",
            ),
            "ai config closed",
        )
    if command == "mouse":
        mode = (args[0].strip().lower() if args else "toggle")
        if mode not in {"on", "off", "toggle"}:
            return CommandResult(state, "mouse command requires on, off, or toggle", handled=False)
        game = dict(state.header_logo_game or {})
        current = bool(game.get("mouse_follow_enabled"))
        if mode == "toggle":
            next_value = not current
        else:
            next_value = mode == "on"
        game["mouse_follow_enabled"] = next_value
        game["movement_mode"] = "mouse_follow" if next_value else "keyboard"
        label = "on" if next_value else "off"
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"mouse-follow {label}"),
            f"mouse-follow {label}",
        )
    if command == "visual":
        return handle_visual_commands(command, args, state)
    if command in {"doc", "md", "markdown"}:
        return handle_visual_commands(command, args, state)
    if command in {"snake-access", "snake_access"}:
        return handle_visual_commands(command, args, state)
    if command == "sources":
        return handle_sources_command(args, state)
    if command == "helpcenter":
        return handle_helpcenter_command(args, state)
    if command == "mail":
        return handle_mail_command(args, state)
    if command == "diff3":
        return handle_diff3_command(args, state)
    if command == "plan":
        return handle_plan_command(args, state)
    if command == "goal":
        return handle_goal_command(args, state)
    if command == "artifact":
        return handle_artifact_command(args, state)
    if command == "ai":
        return handle_ai_command(args, state)
    if command == "inspect":
        return CommandResult(state.with_updates(mode=OperatorMode.INSPECT, status_message="inspect current selection"), "inspect current selection")
    if command == "browser":
        target = args[0] if args else ""
        url = browser_fallback_url(state.endpoint, state.section_id, target)
        return CommandResult(state.with_updates(browser_fallback_url=url, status_message=f"browser fallback {url}"), f"browser fallback {url}")
    if command == "action":
        if not args:
            return CommandResult(state, "action command requires an action name", handled=False)
        risk = args[1] if len(args) > 1 else "read_only"
        action = parse_action(args[0], risk=risk)
        result = dispatch_action(action)
        pending = (
            {
                "name": result.pending_action.name,
                "target": result.pending_action.target,
                "risk": result.pending_action.risk.value,
                "payload": dict(result.pending_action.payload),
                "requires_confirmation": result.pending_action.requires_confirmation,
            }
            if result.pending_action
            else None
        )
        return CommandResult(
            state.with_updates(
                pending_action=pending,
                audit_context=result.audit_context,
                status_message=result.message,
            ),
            result.message,
            handled=result.accepted or result.pending_action is not None,
        )
    if command == "confirm":
        pending = state.pending_action or {}
        if not pending:
            return CommandResult(state, "no pending action to confirm", handled=False)
        action = parse_action(str(pending.get("name") or ""), str(pending.get("target") or ""), str(pending.get("risk") or "high"))
        result = dispatch_action(action, confirmed=True)
        return CommandResult(
            state.with_updates(pending_action=None, audit_context=result.audit_context, status_message=result.message),
            result.message,
            handled=result.accepted,
        )
    if command in {"cancel", "esc"}:
        return CommandResult(
            state.with_updates(mode=OperatorMode.NORMAL, pending_action=None, command_line="", status_message="cancelled"),
            "cancelled",
        )
    if command == "sections":
        return CommandResult(state.with_updates(status_message="sections: " + ",".join(section_ids())), "sections listed")

    # ── speed ─────────────────────────────────────────────────────────────────
    if command == "speed":
        if not args:
            return CommandResult(state, "speed requires a level 1-5", handled=False)
        try:
            level = int(args[0])
        except ValueError:
            return CommandResult(state.with_updates(status_message="speed: ungültiger Wert (1-5)"), "speed: invalid", handled=False)
        if level < 1 or level > 5:
            return CommandResult(state.with_updates(status_message="speed: Wert muss 1-5 sein"), "speed: out of range", handled=False)
        # Map level 1-5 to TPS: 3, 6, 12, 24, 60
        tps_map = {1: 3, 2: 6, 3: 12, 4: 24, 5: 60}
        tps = tps_map[level]
        game = dict(state.header_logo_game or {})
        game["tps_override"] = tps
        game["speed_level"] = level
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"speed: {level}/5 ({tps} tps)"),
            f"speed {level}/5",
        )

    # ── tutor ─────────────────────────────────────────────────────────────────
    if command == "tutor":
        sub = args[0].lower() if args else ""
        if sub == "mode":
            mode_arg = args[1].lower() if len(args) > 1 else ""
            if mode_arg not in {"overview", "deep", "expert"}:
                return CommandResult(state, "tutor mode erwartet: overview | deep | expert", handled=False)
            try:
                from client_surfaces.operator_tui.snake_persistence import set_tutor_mode
                set_tutor_mode(mode_arg)
            except Exception:
                pass
            game = dict(state.header_logo_game or {})
            game["tutor_depth_mode"] = mode_arg
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=f"tutor mode: {mode_arg}"),
                f"tutor mode {mode_arg}",
            )
        if sub == "silent":
            try:
                from client_surfaces.operator_tui.snake_persistence import set_tutor_silent
                set_tutor_silent(True)
            except Exception:
                pass
            game = dict(state.header_logo_game or {})
            game["tutor_silent"] = True
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message="tutor: idle-Kommentare deaktiviert"),
                "tutor silent",
            )
        if sub == "active":
            try:
                from client_surfaces.operator_tui.snake_persistence import set_tutor_silent
                set_tutor_silent(False)
            except Exception:
                pass
            game = dict(state.header_logo_game or {})
            game["tutor_silent"] = False
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message="tutor: idle-Kommentare aktiv"),
                "tutor active",
            )
        if sub == "replay":
            section_arg = args[1].lower() if len(args) > 1 else ""
            try:
                from client_surfaces.operator_tui.snake_persistence import load_tutor_config, save_tutor_config
                cfg = load_tutor_config()
                visited = list(cfg.get("visited_sections") or [])
                if section_arg in visited:
                    visited.remove(section_arg)
                    cfg["visited_sections"] = visited
                    save_tutor_config(cfg)
            except Exception:
                pass
            return CommandResult(
                state.with_updates(status_message=f"tutor replay: {section_arg or '(alle)'} zurückgesetzt"),
                f"tutor replay {section_arg}",
            )
        return CommandResult(state, "tutor: mode <overview|deep|expert> | silent | active | replay <section>", handled=False)

    # ── ask ───────────────────────────────────────────────────────────────────
    if command == "ask":
        question = " ".join(args).strip()
        if not question:
            return CommandResult(state.with_updates(status_message="ask: Bitte Frage angeben"), "ask: leer", handled=False)
        game = dict(state.header_logo_game or {})
        game["tutor_ask_question"] = question
        game["tutor_ask_at"] = __import__("time").monotonic()
        timeout_s = _resolve_chat_ask_timeout_seconds(game)
        game["tutor_ask_timeout_s"] = timeout_s
        game["tutor_ask_deadline_at"] = float(game["tutor_ask_at"]) + timeout_s
        game["tutor_ask_answered"] = False
        game["_ask_submitted"] = False
        game["paused"] = True
        game["active"] = True
        game["alive"] = True
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message=f"ask: {question[:40]}...",
            ),
            f"ask: {question[:40]}",
        )

    # ── rag why ───────────────────────────────────────────────────────────────
    if command == "rag":
        return handle_rag_command(args, state)
    # ── te (task engine) ──────────────────────────────────────────────────────
    if command == "te":
        return handle_te_command(args, state)
    # ── sim ───────────────────────────────────────────────────────────────────
    if command == "sim":
        return handle_sim_command(args, state)
    # ── tutorial ──────────────────────────────────────────────────────────────
    if command == "tutorial":
        return handle_tutorial_command(args, state)
    # ── tutorials ─────────────────────────────────────────────────────────────
    if command == "tutorials":
        return handle_tutorials_command(args, state)
    # ── snakes ────────────────────────────────────────────────────────────────
    if command == "snakes":
        return handle_snakes_command(args, state)
    # ── msg ───────────────────────────────────────────────────────────────────
    if command == "msg":
        return handle_msg_command(args, state)
    # ── chat ──────────────────────────────────────────────────────────────────
    if command == "chat":
        return handle_chat_command(args, state)
    # ── notes ─────────────────────────────────────────────────────────────────
    if command == "notes":
        return handle_notes_command(args, state)
    # ── channels ──────────────────────────────────────────────────────────────
    if command == "channels":
        return handle_channels_command(args, state)
    # ── ai context ────────────────────────────────────────────────────────────
    if command == "ai" and args and args[0].lower() == "context":
        return handle_ai_context_command(args, state)

    # ── run-control ───────────────────────────────────────────────────────────
    if command == "run":
        return handle_run_command(args, state)
    # ── approval ──────────────────────────────────────────────────────────────
    if command == "approval":
        return handle_approval_command(args, state)
    # ── branch ────────────────────────────────────────────────────────────────
    if command == "branch":
        return handle_branch_command(args, state)
    if command == "share":
        return _handle_share_command(args, state)
    if command == "oidc":
        return _handle_oidc_command(args, state)

    # center.browser.webrtc.* commands (Option C — separate from share/webrtc_transport)
    if command in {
        "center.browser.webrtc.start",
        "center.browser.webrtc.stop",
        "center.browser.webrtc.status",
        "center.browser.webrtc.accept_artifact",
    }:
        return _handle_webrtc_command(command, args, state)

    return CommandResult(state.with_updates(status_message=f"unknown command: {command}"), f"unknown command: {command}", handled=False)


