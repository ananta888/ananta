from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any


class OperatorMode(str, Enum):
    NORMAL = "normal"
    COMMAND = "command"
    INSPECT = "inspect"
    EDIT = "edit"


class FocusPane(str, Enum):
    NAVIGATION = "navigation"
    CONTENT = "content"
    DETAIL = "detail"


class PanelState(str, Enum):
    LOADING = "loading"
    HEALTHY = "healthy"
    EMPTY = "empty"
    DEGRADED = "degraded"
    UNAUTHORIZED = "unauthorized"


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    first_class: bool
    primary_dependencies: tuple[str, ...]
    fallback: str
    timeout_seconds: float = 2.0
    refresh_interval_seconds: float = 15.0


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
    panel_states: dict[str, PanelState] | None = None
    section_payloads: dict[str, dict[str, Any]] | None = None
    markdown_source: str = ""

    def with_updates(self, **updates: object) -> "OperatorState":
        return replace(self, **updates)


@dataclass(frozen=True)
class CommandResult:
    state: OperatorState
    message: str
    handled: bool = True


@dataclass(frozen=True)
class SectionLoadResult:
    section_id: str
    state: PanelState
    payload: dict[str, Any]
    message: str = ""


@dataclass(frozen=True)
class RefreshPolicy:
    section_id: str
    timeout_seconds: float
    refresh_interval_seconds: float
    retry_attempts: int = 1


@dataclass(frozen=True)
class Theme:
    name: str
    selected_prefix: str
    idle_prefix: str
    focused_open: str
    focused_close: str
    muted_prefix: str
    warning_prefix: str
