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
from typing import Callable

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
from client_surfaces.operator_tui.commands_ops import handle_ops_command
from client_surfaces.operator_tui.commands_organizations import handle_organization_command
from client_surfaces.operator_tui import commands_core as _core

def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


CommandHandler = _core.CommandHandler


def _args_only(handler: Callable[[list[str], OperatorState], CommandResult]) -> CommandHandler:
    """Adapt a subsystem ``handler(args, state)`` to the registry signature."""
    return lambda command, args, state: handler(args, state)


_WEBRTC_CENTER_COMMANDS = (
    "center.browser.webrtc.start",
    "center.browser.webrtc.stop",
    "center.browser.webrtc.status",
    "center.browser.webrtc.accept_artifact",
)

# (aliases, handler) in the precedence order of the former if/elif chain.
_COMMAND_ROUTES: tuple[tuple[tuple[str, ...], CommandHandler], ...] = (
    (("refresh", "r"), _core.refresh_command),
    (("ops",), _args_only(handle_ops_command)),
    (("org", "organization", "organizations"), _args_only(handle_organization_command)),
    (("section", "open", "goto"), _core.section_command),
    (("next",), _core.next_section_command),
    (("prev",), _core.previous_section_command),
    (("focus",), _core.focus_command),
    (("mode",), _core.mode_command),
    (("help", "?"), _core.help_command),
    (("config", "cfg", "ai-config", "snake-config"), _core.ai_config_command),
    (("mouse",), _core.mouse_command),
    (("visual", "doc", "md", "markdown", "snake-access", "snake_access"), handle_visual_commands),
    (("sources",), _args_only(handle_sources_command)),
    (("helpcenter",), _args_only(handle_helpcenter_command)),
    (("mail",), _args_only(handle_mail_command)),
    (("diff3",), _args_only(handle_diff3_command)),
    (("plan",), _args_only(handle_plan_command)),
    (("goal",), _args_only(handle_goal_command)),
    (("artifact",), _args_only(handle_artifact_command)),
    (("ai",), _args_only(handle_ai_command)),
    (("inspect",), _core.inspect_command),
    (("browser",), _core.browser_command),
    (("action",), _core.action_command),
    (("confirm",), _core.confirm_command),
    (("cancel", "esc"), _core.cancel_command),
    (("sections",), _core.sections_command),
    (("speed",), _core.speed_command),
    (("tutor",), _core.tutor_command),
    (("ask",), _core.ask_command),
    (("rag",), _args_only(handle_rag_command)),
    (("te",), _args_only(handle_te_command)),
    (("sim",), _args_only(handle_sim_command)),
    (("tutorial",), _args_only(handle_tutorial_command)),
    (("tutorials",), _args_only(handle_tutorials_command)),
    (("snakes",), _args_only(handle_snakes_command)),
    (("msg",), _args_only(handle_msg_command)),
    (("chat",), _args_only(handle_chat_command)),
    (("notes",), _args_only(handle_notes_command)),
    (("channels",), _args_only(handle_channels_command)),
    (("run",), _args_only(handle_run_command)),
    (("approval",), _args_only(handle_approval_command)),
    (("branch",), _args_only(handle_branch_command)),
    (("share",), _args_only(_handle_share_command)),
    (("oidc",), _args_only(_handle_oidc_command)),
    (_WEBRTC_CENTER_COMMANDS, _handle_webrtc_command),
)


def _build_command_registry() -> dict[str, CommandHandler]:
    registry: dict[str, CommandHandler] = {}
    for aliases, handler in _COMMAND_ROUTES:
        for alias in aliases:
            registry.setdefault(alias, handler)
    return registry


COMMAND_REGISTRY: dict[str, CommandHandler] = _build_command_registry()


def _strip_command_prefixes(raw_command: str) -> str:
    text = str(raw_command or "").strip()
    while text.startswith(":") or text.startswith("/"):
        text = text[1:].strip()
    return text


def execute_command(raw_command: str, state: OperatorState) -> CommandResult:
    # Dispatch center.browser.* commands first (carbonyl-005)
    browser_result = execute_center_browser_command(raw_command, state)
    if browser_result is not None:
        return browser_result

    text = _strip_command_prefixes(raw_command)
    if not text:
        return CommandResult(state.with_updates(mode=OperatorMode.NORMAL, command_line=""), "empty command ignored")

    parts = text.split()
    command = parts[0].lower()
    handler = COMMAND_REGISTRY.get(command)
    if handler is None:
        return CommandResult(
            state.with_updates(status_message=f"unknown command: {command}"),
            f"unknown command: {command}",
            handled=False,
        )
    return handler(command, parts[1:], state)
