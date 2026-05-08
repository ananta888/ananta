from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class OperatorMode(str, Enum):
    NORMAL = "normal"
    COMMAND = "command"
    INSPECT = "inspect"
    EDIT = "edit"


class FocusPane(str, Enum):
    NAVIGATION = "navigation"
    CONTENT = "content"
    DETAIL = "detail"


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    first_class: bool
    primary_dependencies: tuple[str, ...]
    fallback: str


@dataclass(frozen=True)
class KeyBinding:
    key: str
    action: str
    description: str
    modes: tuple[OperatorMode, ...]


@dataclass(frozen=True)
class OperatorState:
    endpoint: str
    auth_state: str = "unknown"
    mode: OperatorMode = OperatorMode.NORMAL
    focus: FocusPane = FocusPane.NAVIGATION
    section_id: str = "dashboard"
    selected_index: int = 0
    refresh_count: int = 0
    status_message: str = "ready"
    command_line: str = ""
    show_help: bool = False

    def with_updates(self, **updates: object) -> "OperatorState":
        return replace(self, **updates)


@dataclass(frozen=True)
class CommandResult:
    state: OperatorState
    message: str
    handled: bool = True
