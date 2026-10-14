"""Tiny-action-model adapter for multi-tool planning (PDT-003).

Wraps llama.cpp's ``POST /v1/decision`` with the backend-neutral plan contract
(``ananta_contracts.multi_tool_plan``): one request scores every authorized
tool's ``enabled`` field and its closed arguments in a single prefill, and the
plan is assembled outside the model.

The adapter only *proposes* a plan to the tiny router. It reuses the existing
``HttpParallelDecisionRuntime`` and ``CircuitBreaker`` and adds no new HTTP
client. Closed arguments are validated against the exact authorized tool
schemas before a step becomes a call (defense in depth); free text and
unrepresentable arguments stay abstentions, so the caller falls back to the
normal path.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent.services.tiny_router.parallel_decision import CircuitBreaker, HttpParallelDecisionRuntime
from agent.services.tiny_router.types import AdapterRequest, AdapterResult, TinyActionModelProfile
from agent.services.tiny_router.validation import allowed_argument_schemas, validate_json_value
from ananta_contracts.multi_tool_plan import (
    MultiToolPlan,
    PlanSchemaError,
    build_multi_tool_plan_schema,
    plan_request_body,
    read_multi_tool_plan,
)

ADAPTER_ID = "multi_tool_plan"


def registry_argument_validator(
    tools: Sequence[Mapping[str, Any]],
) -> Callable[[str, Mapping[str, Any]], Sequence[str]]:
    """A plan argument validator bound to the exact authorized tool schemas (DIP)."""
    schemas = allowed_argument_schemas(tools)

    def validate(tool: str, arguments: Mapping[str, Any]) -> Sequence[str]:
        schema = schemas.get(tool)
        if schema is None:
            return ["unknown_or_denied_tool"]
        return list(validate_json_value(arguments, schema))

    return validate


class MultiToolPlanAdapter:
    """``TinyActionModelAdapter`` proposing a multi-tool plan through ``/v1/decision``."""

    adapter_id = ADAPTER_ID

    def __init__(
        self,
        runtime: Any | None = None,
        *,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime or HttpParallelDecisionRuntime()
        self._breaker = breaker or CircuitBreaker()
        self._clock = clock

    def is_available(self, profile: TinyActionModelProfile) -> tuple[bool, str]:
        if profile.adapter != self.adapter_id:
            return False, "adapter_profile_mismatch"
        base_url = getattr(self._runtime, "base_url", None)
        if callable(base_url) and not base_url(profile):
            return False, "multi_tool_plan_endpoint_unconfigured"
        if not self._breaker.allow():
            return False, "multi_tool_plan_circuit_open"
        return True, "multi_tool_plan_available"

    def propose(self, request: AdapterRequest) -> AdapterResult:
        started = self._clock()
        open_field = request.profile.metadata.get("open_field") is True
        try:
            schema = build_multi_tool_plan_schema(request.tools, open_field=open_field)
        except PlanSchemaError:
            return AdapterResult("abstain", None, "plan_no_tools", (self._clock() - started) * 1000.0)
        body = plan_request_body(schema, request.prompt, model_id=request.profile.model_id)
        try:
            response = self._runtime.decide(request.profile, body, timeout_ms=request.timeout_ms)
            plan = read_multi_tool_plan(
                response,
                schema,
                min_confidence=request.profile.min_confidence,
                argument_validator=registry_argument_validator(request.tools),
            )
        except Exception:
            self._breaker.record(False)
            raise
        self._breaker.record(True)
        return AdapterResult("candidate", plan, "adapter_completed", (self._clock() - started) * 1000.0)


__all__ = ["ADAPTER_ID", "MultiToolPlanAdapter", "MultiToolPlan", "registry_argument_validator"]
