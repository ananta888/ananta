"""Small ports for candidate generation and telemetry."""
from __future__ import annotations

from typing import Any, Mapping, Protocol

from agent.services.tiny_router.types import (
    AdapterRequest, AdapterResult, RoutingDecision, TinyActionModelProfile,
)


class TinyActionModelAdapter(Protocol):
    adapter_id: str

    def is_available(self, profile: TinyActionModelProfile) -> tuple[bool, str]: ...

    def propose(self, request: AdapterRequest) -> AdapterResult: ...


class ToolInvocationTransport(Protocol):
    """Existing provider transport seam; adapters must not create HTTP clients."""

    def invoke_with_tools(
        self, prompt: str, tools: list[dict[str, Any]], *,
        model: str, timeout_seconds: float,
    ) -> Mapping[str, Any]: ...

    def invoke_text(
        self, prompt: str, *, model: str, timeout_seconds: float,
    ) -> str: ...


class TinyRouterTelemetrySink(Protocol):
    def emit(self, event: Mapping[str, Any]) -> None: ...


class NullTinyRouterTelemetrySink:
    def emit(self, event: Mapping[str, Any]) -> None:
        del event


class RoutingObserver(Protocol):
    def record(self, decision: RoutingDecision, *, prompt_chars: int) -> None: ...
