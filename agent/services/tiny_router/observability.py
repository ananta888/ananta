"""Low-cardinality, argument-free tiny router telemetry."""

from __future__ import annotations

from typing import Any, Mapping

from agent.services.tiny_router.base import TinyRouterTelemetrySink
from agent.services.tiny_router.types import MultiToolRoutingDecision, RoutingDecision


class TinyRouterObserver:
    def __init__(self, sink: TinyRouterTelemetrySink) -> None:
        self._sink = sink

    def record(self, decision: RoutingDecision, *, prompt_chars: int) -> None:
        event: dict[str, Any] = {
            "schema": "ananta.tiny_tool_router_event.v1",
            "status": decision.status,
            "reason_code": decision.reason_code,
            "shadow": decision.shadow,
            "escalation_tier": decision.escalation_tier,
            "elapsed_ms": round(decision.elapsed_ms, 3),
            "prompt_chars": max(0, int(prompt_chars)),
            "attempt_count": len(decision.attempts),
            "attempts": [attempt.as_dict() for attempt in decision.attempts],
        }
        if decision.candidate:
            event.update(
                {
                    "profile_id": decision.candidate.profile_id,
                    "adapter_id": decision.candidate.adapter_id,
                    "tool_name": decision.candidate.tool_name,
                    "confidence": decision.candidate.confidence,
                }
            )
        self._sink.emit(event)

    def record_plan(self, decision: MultiToolRoutingDecision, *, prompt_chars: int) -> None:
        call_names = []
        if decision.plan is not None:
            call_names = [step.tool for step in decision.plan.calls()]
        self._sink.emit(
            {
                "schema": "ananta.tiny_multi_tool_router_event.v1",
                "status": decision.status,
                "reason_code": decision.reason_code,
                "shadow": decision.shadow,
                "escalation_tier": decision.escalation_tier,
                "elapsed_ms": round(decision.elapsed_ms, 3),
                "prompt_chars": max(0, int(prompt_chars)),
                "attempt_count": len(decision.attempts),
                "attempts": [attempt.as_dict() for attempt in decision.attempts],
                "call_count": len(call_names),
                "call_tools": call_names,
            }
        )


class ListTelemetrySink:
    def __init__(self) -> None:
        self.events: list[Mapping[str, Any]] = []

    def emit(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))
