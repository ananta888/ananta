"""Command Pattern for heuristic UI actions.

HeuristicCommand ABC maps DecisionResult.action_kind → concrete Command.
Adapters (TuiCommandAdapter, EclipseCommandAdapter) execute commands without
embedding UI logic in the command itself, keeping commands unit-testable.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class CommandResult:
    success: bool
    message: str = ""
    output: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def ok(message: str = "", **output: Any) -> "CommandResult":
        return CommandResult(success=True, message=message, output=dict(output))

    @staticmethod
    def fail(message: str, **output: Any) -> "CommandResult":
        return CommandResult(success=False, message=message, output=dict(output))


# ── Adapter protocol ──────────────────────────────────────────────────────────

class CommandAdapter(abc.ABC):
    """Executes UI effects. Subclassed for TUI and Eclipse — never called directly."""

    @abc.abstractmethod
    def show_hint(self, text: str, duration_ms: int) -> None: ...

    @abc.abstractmethod
    def open_chat(self) -> None: ...

    @abc.abstractmethod
    def show_context_summary(self, refs: list[str]) -> None: ...

    @abc.abstractmethod
    def open_source_ref(self, ref: str) -> None: ...

    @abc.abstractmethod
    def request_scope(self) -> None: ...

    @abc.abstractmethod
    def move_snake(self, dx: int, dy: int) -> None: ...

    @abc.abstractmethod
    def set_lurk_mode(self, zone: str | None) -> None: ...


# ── Base command ──────────────────────────────────────────────────────────────

class HeuristicCommand(abc.ABC):
    """Abstract command. Subclasses implement execute(); undo() is optional."""

    @abc.abstractmethod
    def execute(self, adapter: CommandAdapter) -> CommandResult: ...

    def undo(self, adapter: CommandAdapter) -> bool:
        return False

    @abc.abstractmethod
    def to_dict(self) -> dict[str, Any]: ...


# ── Concrete commands ─────────────────────────────────────────────────────────

@dataclass
class FollowWithDistanceCommand(HeuristicCommand):
    target_x: int = 0
    target_y: int = 0
    distance: int = 4
    dx: int = 1
    dy: int = 0

    def execute(self, adapter: CommandAdapter) -> CommandResult:
        adapter.move_snake(self.dx, self.dy)
        return CommandResult.ok("follow", dx=self.dx, dy=self.dy)

    def to_dict(self) -> dict[str, Any]:
        return {"command": "follow_with_distance", "target_x": self.target_x, "target_y": self.target_y,
                "distance": self.distance, "dx": self.dx, "dy": self.dy}


@dataclass
class LurkNearCommand(HeuristicCommand):
    zone: str | None = None

    def execute(self, adapter: CommandAdapter) -> CommandResult:
        adapter.set_lurk_mode(self.zone)
        return CommandResult.ok("lurk", zone=self.zone or "")

    def to_dict(self) -> dict[str, Any]:
        return {"command": "lurk_near", "zone": self.zone}


@dataclass
class ShowHintCommand(HeuristicCommand):
    hint_text: str = ""
    duration_ms: int = 3000

    def execute(self, adapter: CommandAdapter) -> CommandResult:
        adapter.show_hint(self.hint_text, self.duration_ms)
        return CommandResult.ok("hint_shown")

    def to_dict(self) -> dict[str, Any]:
        return {"command": "show_hint", "hint_text": self.hint_text, "duration_ms": self.duration_ms}


class OpenChatCommand(HeuristicCommand):
    def execute(self, adapter: CommandAdapter) -> CommandResult:
        adapter.open_chat()
        return CommandResult.ok("chat_opened")

    def to_dict(self) -> dict[str, Any]:
        return {"command": "open_chat"}


@dataclass
class ShowContextSummaryCommand(HeuristicCommand):
    refs: list[str] = field(default_factory=list)

    def execute(self, adapter: CommandAdapter) -> CommandResult:
        adapter.show_context_summary(self.refs)
        return CommandResult.ok("context_summary_shown", refs=self.refs)

    def to_dict(self) -> dict[str, Any]:
        return {"command": "show_context_summary", "refs": list(self.refs)}


@dataclass
class OpenSourceRefCommand(HeuristicCommand):
    ref: str = ""

    def execute(self, adapter: CommandAdapter) -> CommandResult:
        adapter.open_source_ref(self.ref)
        return CommandResult.ok("source_ref_opened", ref=self.ref)

    def to_dict(self) -> dict[str, Any]:
        return {"command": "open_source_ref", "ref": self.ref}


class AskScopeCommand(HeuristicCommand):
    def execute(self, adapter: CommandAdapter) -> CommandResult:
        adapter.request_scope()
        return CommandResult.ok("scope_requested")

    def to_dict(self) -> dict[str, Any]:
        return {"command": "ask_scope"}


class NoActionCommand(HeuristicCommand):
    def execute(self, adapter: CommandAdapter) -> CommandResult:
        return CommandResult.ok("no_action")

    def to_dict(self) -> dict[str, Any]:
        return {"command": "no_action"}


# ── Adapters ──────────────────────────────────────────────────────────────────

class MockCommandAdapter(CommandAdapter):
    """In-memory adapter for tests — records all calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def show_hint(self, text: str, duration_ms: int) -> None:
        self.calls.append({"method": "show_hint", "text": text, "duration_ms": duration_ms})

    def open_chat(self) -> None:
        self.calls.append({"method": "open_chat"})

    def show_context_summary(self, refs: list[str]) -> None:
        self.calls.append({"method": "show_context_summary", "refs": refs})

    def open_source_ref(self, ref: str) -> None:
        self.calls.append({"method": "open_source_ref", "ref": ref})

    def request_scope(self) -> None:
        self.calls.append({"method": "request_scope"})

    def move_snake(self, dx: int, dy: int) -> None:
        self.calls.append({"method": "move_snake", "dx": dx, "dy": dy})

    def set_lurk_mode(self, zone: str | None) -> None:
        self.calls.append({"method": "set_lurk_mode", "zone": zone})


class TuiCommandAdapter(CommandAdapter):
    """Adapter that executes commands against TUI state dict (no direct UI import)."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self._state = state if state is not None else {}

    def show_hint(self, text: str, duration_ms: int) -> None:
        self._state["status_message"] = text
        self._state["hint_duration_ms"] = duration_ms

    def open_chat(self) -> None:
        self._state["chat_focus"] = True

    def show_context_summary(self, refs: list[str]) -> None:
        self._state["context_summary_refs"] = list(refs)

    def open_source_ref(self, ref: str) -> None:
        self._state["open_ref"] = ref

    def request_scope(self) -> None:
        self._state["scope_requested"] = True

    def move_snake(self, dx: int, dy: int) -> None:
        self._state["snake_dx"] = dx
        self._state["snake_dy"] = dy

    def set_lurk_mode(self, zone: str | None) -> None:
        self._state["lurk_mode"] = True
        self._state["lurk_zone"] = zone


class EclipseCommandAdapter(CommandAdapter):
    """Adapter for Eclipse plugin — emits commands as dicts for Hub API relay."""

    def __init__(self) -> None:
        self._pending: list[dict[str, Any]] = []

    def show_hint(self, text: str, duration_ms: int) -> None:
        self._pending.append({"type": "SHOW_HINT", "text": text, "durationMs": duration_ms})

    def open_chat(self) -> None:
        self._pending.append({"type": "OPEN_CHAT"})

    def show_context_summary(self, refs: list[str]) -> None:
        self._pending.append({"type": "SHOW_CONTEXT", "refs": refs})

    def open_source_ref(self, ref: str) -> None:
        self._pending.append({"type": "OPEN_REF", "ref": ref})

    def request_scope(self) -> None:
        self._pending.append({"type": "REQUEST_SCOPE"})

    def move_snake(self, dx: int, dy: int) -> None:
        self._pending.append({"type": "MOVE_SNAKE", "dx": dx, "dy": dy})

    def set_lurk_mode(self, zone: str | None) -> None:
        self._pending.append({"type": "SET_LURK", "zone": zone})

    def flush(self) -> list[dict[str, Any]]:
        cmds, self._pending = list(self._pending), []
        return cmds


# ── Decision → Command mapping ────────────────────────────────────────────────

def command_for_decision(decision_result: Any) -> HeuristicCommand:
    """Map DecisionResult.action_kind → HeuristicCommand instance."""
    action = getattr(decision_result, "action_kind", "no_action")
    if action == "follow":
        motion = getattr(decision_result, "suggested_motion", None)
        dx = getattr(motion, "dx", 0) if motion else 0
        dy = getattr(motion, "dy", 0) if motion else 0
        return FollowWithDistanceCommand(dx=dx, dy=dy)
    if action == "lurk":
        return LurkNearCommand()
    if action == "explain":
        return ShowHintCommand(hint_text="Erklärung angefordert")
    if action == "chat":
        refs = getattr(decision_result, "selected_context_refs", [])
        if refs:
            return ShowContextSummaryCommand(refs=list(refs))
        return OpenChatCommand()
    if action == "no_action":
        return NoActionCommand()
    if action == "policy_denied":
        return ShowHintCommand(hint_text="Aktion blockiert durch Policy")
    return NoActionCommand()
