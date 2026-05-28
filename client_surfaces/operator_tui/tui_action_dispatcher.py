from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TuiAction:
    action_id: str
    label: str
    description: str
    risk: str           # "safe" | "medium" | "high"
    category: str       # "view" | "overlay" | "focus" | "artifact" | "snake" | "help"
    allowed_modes: frozenset[str]


@dataclass(frozen=True)
class ActionRequest:
    action_id: str
    args: dict[str, Any]
    source: str         # "chat" | "test" | "keyboard"


@dataclass(frozen=True)
class DispatchResult:
    status: str         # "ok" | "error" | "denied" | "not_found"
    action_id: str
    message: str
    changed_state_summary: dict[str, Any]
    control_result_marker: dict[str, Any]

    def is_ok(self) -> bool:
        return self.status == "ok"


_INITIAL_ACTIONS: tuple[TuiAction, ...] = (
    TuiAction("help.tui", "TUI Help", "Show available TUI control commands", "safe", "help", frozenset({"any"})),
    TuiAction("view.list", "View List", "List all center viewport views", "safe", "view", frozenset({"any"})),
    TuiAction("view.next", "Next View", "Switch to next available center view", "safe", "view", frozenset({"any"})),
    TuiAction("view.previous", "Previous View", "Switch to previous available center view", "safe", "view", frozenset({"any"})),
    TuiAction("view.select", "Select View", "Switch to named center view", "safe", "view", frozenset({"any"})),
    TuiAction("overlay.views.on", "Show View Overlay", "Show the two-line view switcher overlay", "safe", "overlay", frozenset({"any"})),
    TuiAction("overlay.views.off", "Hide View Overlay", "Hide the two-line view switcher overlay", "safe", "overlay", frozenset({"any"})),
    TuiAction("overlay.views.toggle", "Toggle View Overlay", "Toggle the view switcher overlay visibility", "safe", "overlay", frozenset({"any"})),
    TuiAction("focus.chat", "Focus Chat", "Move input focus to chat panel", "safe", "focus", frozenset({"any"})),
    TuiAction("focus.artifacts", "Focus Artifacts", "Move focus to artifacts panel", "safe", "focus", frozenset({"any"})),
    TuiAction("focus.main", "Focus Main", "Move focus to main content area", "safe", "focus", frozenset({"any"})),
    TuiAction("focus.diagnostics", "Focus Diagnostics", "Move focus to diagnostics view", "safe", "focus", frozenset({"any"})),
    TuiAction("artifact.open", "Open Artifact", "Open artifact by index or id", "safe", "artifact", frozenset({"any"})),
    TuiAction("snake.pause", "Snake Pause", "Pause/resume snake game", "safe", "snake", frozenset({"any"})),
    TuiAction("snake.resume", "Snake Resume", "Resume snake game", "safe", "snake", frozenset({"any"})),
    TuiAction("snake.follow.on", "Snake Follow On", "Enable mouse follow mode", "safe", "snake", frozenset({"any"})),
    TuiAction("snake.follow.off", "Snake Follow Off", "Disable mouse follow mode", "safe", "snake", frozenset({"any"})),
)

_FORBIDDEN_CATEGORIES: frozenset[str] = frozenset({"shell", "file_write", "file_delete", "network", "destructive"})


class TuiActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, TuiAction] = {}
        for action in _INITIAL_ACTIONS:
            self._actions[action.action_id] = action

    def get(self, action_id: str) -> TuiAction | None:
        return self._actions.get(action_id)

    def all_actions(self) -> list[TuiAction]:
        return sorted(self._actions.values(), key=lambda a: a.action_id)

    def help_text(self) -> str:
        lines = ["TUI Chat Control Commands:", ""]
        for action in self.all_actions():
            lines.append(f"  /{action.label:28s} {action.description}")
        lines.append("")
        lines.append("Note: shell, file and destructive operations are intentionally out of scope.")
        return "\n".join(lines)


_registry = TuiActionRegistry()


def get_registry() -> TuiActionRegistry:
    return _registry


class TuiActionDispatcher:
    def __init__(self, *, registry: TuiActionRegistry | None = None) -> None:
        self._registry = registry or get_registry()
        self._tui_state: dict[str, Any] = {}

    def set_tui_state(self, state: dict[str, Any]) -> None:
        self._tui_state = dict(state)

    def dispatch(self, request: ActionRequest) -> DispatchResult:
        try:
            action = self._registry.get(request.action_id)
        except Exception as exc:
            return DispatchResult(
                status="error",
                action_id=request.action_id,
                message=f"Registry lookup failed: {exc}",
                changed_state_summary={},
                control_result_marker={"status": "error", "action_id": request.action_id, "error": str(exc)},
            )
        if action is None:
            return DispatchResult(
                status="not_found",
                action_id=request.action_id,
                message=f"Unknown action {request.action_id!r}. Try /help tui.",
                changed_state_summary={},
                control_result_marker={"status": "not_found", "action_id": request.action_id},
            )
        if action.category in _FORBIDDEN_CATEGORIES:
            return DispatchResult(
                status="denied",
                action_id=request.action_id,
                message=f"Action category {action.category!r} is forbidden.",
                changed_state_summary={},
                control_result_marker={"status": "denied", "action_id": request.action_id, "reason_code": "forbidden_category"},
            )
        try:
            return self._execute(action, request)
        except Exception as exc:
            return DispatchResult(
                status="error",
                action_id=request.action_id,
                message=f"Action failed: {exc}",
                changed_state_summary={},
                control_result_marker={"status": "error", "action_id": request.action_id, "error": str(exc)},
            )

    def _execute(self, action: TuiAction, request: ActionRequest) -> DispatchResult:
        state = dict(self._tui_state)
        changed: dict[str, Any] = {}

        if action.action_id == "help.tui":
            return DispatchResult(
                status="ok", action_id=action.action_id,
                message=self._registry.help_text(),
                changed_state_summary={},
                control_result_marker={"status": "ok", "action_id": action.action_id},
            )

        if action.action_id == "view.list":
            views = state.get("available_views") or []
            active = state.get("active_view") or state.get("visual_viewport_active_view") or ""
            view_list = ", ".join(str(v) for v in views) or "(none)"
            return DispatchResult(
                status="ok", action_id=action.action_id,
                message=f"Available views: {view_list}. Active: {active or '(none)'}",
                changed_state_summary={},
                control_result_marker={"status": "ok", "action_id": action.action_id, "views": views, "active": active},
            )

        if action.action_id in ("view.next", "view.previous"):
            direction = "next" if action.action_id == "view.next" else "previous"
            changed[f"visual_viewport_cycle_{direction}"] = True
            return DispatchResult(
                status="ok", action_id=action.action_id,
                message=f"Switching to {direction} view",
                changed_state_summary=changed,
                control_result_marker={"status": "ok", "action_id": action.action_id, "direction": direction},
            )

        if action.action_id == "view.select":
            view_id = str(request.args.get("view_id") or "")
            if not view_id:
                return DispatchResult(
                    status="error", action_id=action.action_id, message="view_id argument required",
                    changed_state_summary={},
                    control_result_marker={"status": "error", "action_id": action.action_id, "reason_code": "missing_arg"},
                )
            changed["visual_viewport_active_view_request"] = view_id
            return DispatchResult(
                status="ok", action_id=action.action_id,
                message=f"View switched to {view_id}",
                changed_state_summary=changed,
                control_result_marker={"status": "ok", "action_id": action.action_id, "view_id": view_id},
            )

        if action.action_id in ("overlay.views.on", "overlay.views.off", "overlay.views.toggle"):
            current = bool(state.get("visual_view_switcher_overlay_visible", False))
            if action.action_id == "overlay.views.on":
                new_val = True
            elif action.action_id == "overlay.views.off":
                new_val = False
            else:
                new_val = not current
            changed["visual_view_switcher_overlay_visible"] = new_val
            return DispatchResult(
                status="ok", action_id=action.action_id,
                message=f"View overlay {'shown' if new_val else 'hidden'}",
                changed_state_summary=changed,
                control_result_marker={"status": "ok", "action_id": action.action_id, "overlay_visible": new_val},
            )

        if action.action_id.startswith("focus."):
            target = action.action_id[len("focus."):]
            changed["focus_target_request"] = target
            return DispatchResult(
                status="ok", action_id=action.action_id,
                message=f"Focus moved to: {target}",
                changed_state_summary=changed,
                control_result_marker={"status": "ok", "action_id": action.action_id, "focus": target},
            )

        if action.action_id == "artifact.open":
            ref = str(request.args.get("ref") or "")
            if not ref:
                return DispatchResult(
                    status="error", action_id=action.action_id, message="artifact ref required",
                    changed_state_summary={},
                    control_result_marker={"status": "error", "action_id": action.action_id, "reason_code": "missing_arg"},
                )
            changed["open_artifact_request"] = ref
            return DispatchResult(
                status="ok", action_id=action.action_id,
                message=f"Opening artifact: {ref}",
                changed_state_summary=changed,
                control_result_marker={"status": "ok", "action_id": action.action_id, "ref": ref},
            )

        if action.action_id in ("snake.pause", "snake.resume"):
            paused = action.action_id == "snake.pause"
            changed["snake_paused_request"] = paused
            return DispatchResult(
                status="ok", action_id=action.action_id,
                message="Snake paused" if paused else "Snake resumed",
                changed_state_summary=changed,
                control_result_marker={"status": "ok", "action_id": action.action_id, "paused": paused},
            )

        if action.action_id in ("snake.follow.on", "snake.follow.off"):
            follow_on = action.action_id == "snake.follow.on"
            changed["snake_mouse_follow_request"] = follow_on
            return DispatchResult(
                status="ok", action_id=action.action_id,
                message=f"Snake mouse follow {'enabled' if follow_on else 'disabled'}",
                changed_state_summary=changed,
                control_result_marker={"status": "ok", "action_id": action.action_id, "follow": follow_on},
            )

        return DispatchResult(
            status="error", action_id=action.action_id,
            message=f"No handler for action {action.action_id!r}",
            changed_state_summary={},
            control_result_marker={"status": "error", "action_id": action.action_id, "reason_code": "no_handler"},
        )
