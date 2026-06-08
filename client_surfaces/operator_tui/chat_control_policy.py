from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from client_surfaces.operator_tui.chat_control_config import ChatControlConfig
from client_surfaces.operator_tui.chat_control_parser import ParsedCommand
from client_surfaces.operator_tui.tui_action_dispatcher import TuiActionRegistry, _FORBIDDEN_CATEGORIES, get_registry


@dataclass(frozen=True)
class PolicyDecision:
    verdict: str            # "allow" | "deny" | "require_confirmation"
    action_id: str
    normalized_args: dict[str, Any]
    reason: str
    mode: str
    auto_confirmed: bool = False

    def allowed(self) -> bool:
        return self.verdict == "allow"


_pending_confirmations: dict[str, ParsedCommand] = {}


def evaluate(
    parsed: ParsedCommand,
    *,
    config: ChatControlConfig,
    registry: TuiActionRegistry | None = None,
    context_state: dict[str, Any] | None = None,
) -> PolicyDecision:
    reg = registry or get_registry()

    if not parsed.ok or not parsed.action_id:
        return PolicyDecision(
            verdict="deny", action_id="", normalized_args={},
            reason=parsed.error or "unknown command",
            mode=config.mode,
        )

    action = reg.get(parsed.action_id)
    if action is None:
        return PolicyDecision(
            verdict="deny", action_id=parsed.action_id, normalized_args={},
            reason=f"action {parsed.action_id!r} not registered (default deny)",
            mode=config.mode,
        )

    if action.category in _FORBIDDEN_CATEGORIES:
        return PolicyDecision(
            verdict="deny", action_id=parsed.action_id, normalized_args={},
            reason=f"category {action.category!r} is forbidden in all modes",
            mode=config.mode,
        )

    normalized_args = _normalize_args(parsed, action.action_id)

    if config.is_autonomous_e2e:
        if parsed.action_id in config.e2e_allowlist and action.risk == "safe":
            return PolicyDecision(
                verdict="allow", action_id=parsed.action_id, normalized_args=normalized_args,
                reason="autonomous_e2e: allowlisted safe action",
                mode=config.mode, auto_confirmed=True,
            )
        return PolicyDecision(
            verdict="deny", action_id=parsed.action_id, normalized_args={},
            reason=f"autonomous_e2e: {parsed.action_id!r} not in allowlist or not safe",
            mode=config.mode,
        )

    # interactive_safe
    if action.risk == "safe":
        return PolicyDecision(
            verdict="allow", action_id=parsed.action_id, normalized_args=normalized_args,
            reason="safe action allowed", mode=config.mode,
        )
    if action.risk == "medium":
        return PolicyDecision(
            verdict="require_confirmation", action_id=parsed.action_id, normalized_args=normalized_args,
            reason=f"{parsed.action_id!r} requires confirmation (risk: medium)", mode=config.mode,
        )
    return PolicyDecision(
        verdict="deny", action_id=parsed.action_id, normalized_args={},
        reason=f"{parsed.action_id!r} is high-risk and denied in interactive_safe mode", mode=config.mode,
    )


def handle_confirmation(text: str, *, channel_id: str, config: ChatControlConfig) -> tuple[str | None, ParsedCommand | None]:
    t = text.strip().lower()
    if t in ("/yes", "/y"):
        pending = _pending_confirmations.pop(channel_id, None)
        if pending:
            return "confirm", pending
    if t in ("/no", "/n", "/cancel"):
        _pending_confirmations.pop(channel_id, None)
        return "cancel", None
    return None, None


def set_pending_confirmation(channel_id: str, command: ParsedCommand) -> None:
    _pending_confirmations[channel_id] = command


def has_pending_confirmation(channel_id: str) -> bool:
    return channel_id in _pending_confirmations


def _normalize_args(parsed: ParsedCommand, action_id: str) -> dict[str, Any]:
    if action_id == "view.select" and parsed.args:
        return {"view_id": parsed.args[0]}
    if action_id == "artifact.open" and parsed.args:
        return {"ref": parsed.args[0]}
    if action_id == "session.new" and parsed.args:
        return {"name": parsed.args[0]}
    if action_id == "session.delete" and parsed.args:
        return {"session_id": parsed.args[0]}
    if action_id == "session.switch" and parsed.args:
        return {"session_id": parsed.args[0]}
    if action_id == "session.rename" and len(parsed.args) >= 2:
        return {"session_id": parsed.args[0], "name": parsed.args[1]}
    if action_id == "session.clear":
        # args[0] is the optional target (session id or "all"); if absent
        # the dispatcher treats it as "active session".
        target = parsed.args[0] if parsed.args else ""
        if target.lower() == "all":
            return {"target": "all"}
        return {"target": target}  # may be empty → active session
    return {}
