from __future__ import annotations

from typing import Any, Protocol


class PolicyPort(Protocol):
    def classify_command(self, *, command: str, profile: str) -> dict[str, Any]: ...


class TracePort(Protocol):
    def emit(self, *, event_type: str, payload: dict[str, Any]) -> None: ...


class ArtifactPort(Protocol):
    def publish(self, *, artifact: dict[str, Any]) -> dict[str, Any]: ...

