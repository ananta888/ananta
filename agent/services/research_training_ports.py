"""Small ports shared by research-training composition roots."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class ResearchStageExecutorPort(Protocol):
    def execute(self, *, run_spec: Mapping[str, Any], stage: Mapping[str, Any], attempt_id: str) -> dict[str, Any]: ...


class ResearchArtifactPublisherPort(Protocol):
    def publish(self, *, manifest: Mapping[str, Any], content: bytes) -> dict[str, Any]: ...


class ResearchTelemetryPort(Protocol):
    def record(self, *, event: Mapping[str, Any]) -> None: ...


__all__ = ["ResearchArtifactPublisherPort", "ResearchStageExecutorPort", "ResearchTelemetryPort"]
