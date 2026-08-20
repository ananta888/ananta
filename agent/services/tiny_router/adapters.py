"""Candidate-only adapters over existing runtimes and provider transports."""
from __future__ import annotations

import importlib.util
import json
import time
from typing import Any, Callable, Mapping, Sequence

from agent.services.tiny_router.base import ToolInvocationTransport
from agent.services.tiny_router.schema_dialects import ToolSchemaDialectAdapter
from agent.services.tiny_router.types import AdapterRequest, AdapterResult, TinyActionModelProfile


class ModelInvocationTransport:
    """Reuses ModelInvocationService; deliberately owns no network client."""

    def invoke_with_tools(
        self, prompt: str, tools: list[dict[str, Any]], *, model: str,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        from agent.services.model_invocation_service import ModelInvocationService
        return ModelInvocationService.invoke_with_tools(
            prompt, tools, model=model, timeout=timeout_seconds,
            retry_on_contract_error=True,
        )

    def invoke_text(
        self, prompt: str, *, model: str, timeout_seconds: float,
    ) -> str:
        from agent.services.model_invocation_service import ModelInvocationService
        return ModelInvocationService.invoke(prompt, model=model, timeout=timeout_seconds)


class OpenAICompatibleActionAdapter:
    adapter_id = "openai_compatible"

    def __init__(
        self, transport: ToolInvocationTransport | None = None, *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport or ModelInvocationTransport()
        self._clock = clock
        self._dialects = ToolSchemaDialectAdapter()

    def is_available(self, profile: TinyActionModelProfile) -> tuple[bool, str]:
        return (
            (True, "transport_available") if profile.adapter == self.adapter_id
            else (False, "adapter_profile_mismatch")
        )

    def propose(self, request: AdapterRequest) -> AdapterResult:
        started = self._clock()
        projection = self._dialects.project(request.tools, dialect=request.profile.dialect)
        timeout_seconds = max(0.001, request.timeout_ms / 1000.0)
        if request.profile.dialect == "xlam":
            payload: Any = self._transport.invoke_text(
                self._xlam_prompt(request.prompt, projection.tools),
                model=request.profile.model_id, timeout_seconds=timeout_seconds,
            )
        else:
            payload = self._transport.invoke_with_tools(
                request.prompt, [dict(item) for item in projection.tools],
                model=request.profile.model_id, timeout_seconds=timeout_seconds,
            )
        return AdapterResult(
            "candidate", payload,
            "schema_projection_lossy" if projection.losses else "adapter_completed",
            (self._clock() - started) * 1000.0,
        )

    @staticmethod
    def _xlam_prompt(prompt: str, tools: Sequence[Mapping[str, Any]]) -> str:
        contract = {
            "instruction": (
                "Select only from the supplied tools. Return one JSON object "
                "with tool_calls, or an empty tool_calls array. Never execute."
            ),
            "tools": list(tools), "query": prompt,
            "output": {"tool_calls": [{"name": "allowed_name", "arguments": {"key": "value"}}]},
        }
        return json.dumps(contract, sort_keys=True, separators=(",", ":"))


class CactusNeedleRuntime:
    """Optional local Needle runtime using complete(), never run()."""

    def is_available(self) -> tuple[bool, str]:
        return (
            (True, "needle_dependency_available")
            if importlib.util.find_spec("needle") is not None
            else (False, "needle_dependency_missing")
        )

    def complete(
        self, *, prompt: str, tools: list[dict[str, Any]],
        profile: TinyActionModelProfile, timeout_ms: int,
    ) -> Mapping[str, Any]:
        del timeout_ms
        import needle
        weights = str(profile.metadata.get("weights") or "").strip() or None
        agent = needle.Needle(tools=tools, weights=weights)
        return agent.complete(prompt)


class NeedleCandidateAdapter:
    adapter_id = "needle"

    def __init__(
        self, runtime: CactusNeedleRuntime | Any | None = None, *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime or CactusNeedleRuntime()
        self._clock = clock
        self._dialects = ToolSchemaDialectAdapter()

    def is_available(self, profile: TinyActionModelProfile) -> tuple[bool, str]:
        if profile.adapter != self.adapter_id:
            return False, "adapter_profile_mismatch"
        return self._runtime.is_available()

    def propose(self, request: AdapterRequest) -> AdapterResult:
        started = self._clock()
        projection = self._dialects.project(request.tools, dialect="needle")
        payload = self._runtime.complete(
            prompt=request.prompt, tools=[dict(item) for item in projection.tools],
            profile=request.profile, timeout_ms=request.timeout_ms,
        )
        return AdapterResult(
            "candidate", payload, "adapter_completed",
            (self._clock() - started) * 1000.0,
        )
