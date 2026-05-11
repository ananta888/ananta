from __future__ import annotations

from typing import Any, Protocol


class PolicyPort(Protocol):
    # T001: hub_decision flows from Hub envelope, not a worker-local stub
    def classify_command(self, *, command: str, profile: str, hub_decision: str = "allow") -> dict[str, Any]: ...


class TracePort(Protocol):
    def emit(self, *, event_type: str, payload: dict[str, Any]) -> None: ...


class ArtifactPort(Protocol):
    def publish(self, *, artifact: dict[str, Any]) -> dict[str, Any]: ...

