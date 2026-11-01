from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from client_surfaces.operator_tui import commands_core as _core
from client_surfaces.operator_tui.commands_ai import handle_ai_command
from client_surfaces.operator_tui.commands_chat import (
    handle_channels_command,
    handle_chat_command,
    handle_notes_command,
)
from client_surfaces.operator_tui.commands_goal import handle_artifact_command, handle_goal_command
from client_surfaces.operator_tui.commands_helpcenter import handle_helpcenter_command
from client_surfaces.operator_tui.commands_mail import handle_mail_command
from client_surfaces.operator_tui.commands_memory_audit import handle_memory_audit_command
from client_surfaces.operator_tui.commands_oidc import _handle_oidc_command
from client_surfaces.operator_tui.commands_ops import handle_ops_command
from client_surfaces.operator_tui.commands_organizations import handle_organization_command
from client_surfaces.operator_tui.commands_planning import handle_diff3_command, handle_plan_command
from client_surfaces.operator_tui.commands_rag import (
    handle_msg_command,
    handle_rag_command,
    handle_sim_command,
    handle_snakes_command,
    handle_te_command,
    handle_tutorial_command,
    handle_tutorials_command,
)
from client_surfaces.operator_tui.commands_run_control import (
    handle_approval_command,
    handle_branch_command,
    handle_run_command,
)
from client_surfaces.operator_tui.commands_share import _handle_share_command
from client_surfaces.operator_tui.commands_sources import handle_sources_command
from client_surfaces.operator_tui.commands_visual import handle_visual_commands
from client_surfaces.operator_tui.commands_webrtc import _handle_webrtc_command, execute_center_browser_command
from client_surfaces.operator_tui.diff.ai_diff_dispatch import (  # noqa: F401  (call-time resolution seam)
    dispatch_ai_diff_request,
)
from client_surfaces.operator_tui.models import CommandResult, OperatorMode, OperatorState


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
    (("memory-audit",), _args_only(handle_memory_audit_command)),
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
