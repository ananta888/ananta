from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class ExternalWindowState(str, Enum):
    INACTIVE = "inactive"
    STARTING = "starting"
    ACTIVE = "active"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True)
class WindowHealth:
    state: ExternalWindowState
    backend: str
    pid: int | None = None
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class WindowSurface(Protocol):
    backend_name: str

    def open_window(self, *, url: str) -> WindowHealth:
        ...

    def close_window(self) -> WindowHealth:
        ...

    def health(self) -> WindowHealth:
        ...
