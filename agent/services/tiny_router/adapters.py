"""Candidate-only adapters over existing runtimes and provider transports."""
from __future__ import annotations

import importlib.util
import json
import os
import time
import urllib.request
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
        weights_env = str(profile.metadata.get("weights_env") or "").strip()
        weights = str(
            (os.environ.get(weights_env) if weights_env else None)
            or profile.metadata.get("weights")
            or ""
        ).strip() or None
        agent = needle.Needle(tools=tools, weights=weights)
        return agent.complete(prompt)


class HttpNeedleRuntime:
    """Authenticated transport to the host-side candidate-only runtime."""

    def __init__(self, *, opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        self._opener = opener

    @staticmethod
    def _settings(profile: TinyActionModelProfile) -> tuple[str, str]:
        endpoint_env = str(profile.metadata.get("endpoint_env") or "").strip()
        token_env = str(profile.metadata.get("token_env") or "").strip()
        endpoint = str(os.environ.get(endpoint_env) if endpoint_env else "").strip()
        token = str(os.environ.get(token_env) if token_env else "").strip()
        return endpoint.rstrip("/"), token

    def is_available(self, profile: TinyActionModelProfile) -> tuple[bool, str]:
        endpoint, token = self._settings(profile)
        if not endpoint:
            return False, "needle_endpoint_missing"
        if len(token) < 24:
            return False, "needle_token_missing"
        return True, "needle_sidecar_configured"

    def complete(
        self, *, prompt: str, tools: list[dict[str, Any]],
        profile: TinyActionModelProfile, timeout_ms: int,
    ) -> Mapping[str, Any]:
        endpoint, token = self._settings(profile)
        body = json.dumps({"prompt": prompt, "tools": tools}).encode("utf-8")
        request = urllib.request.Request(
            endpoint + "/internal/v1/candidates",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self._opener(request, timeout=max(0.001, timeout_ms / 1000.0)) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, Mapping) or "candidate" not in payload:
            raise ValueError("needle_sidecar_response_invalid")
        candidate = payload["candidate"]
        if not isinstance(candidate, Mapping):
            raise ValueError("needle_sidecar_candidate_invalid")
        return candidate


class NeedleCandidateAdapter:
    adapter_id = "needle"

    def __init__(
        self, runtime: CactusNeedleRuntime | HttpNeedleRuntime | Any | None = None, *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime
        self._clock = clock
        self._dialects = ToolSchemaDialectAdapter()

    def is_available(self, profile: TinyActionModelProfile) -> tuple[bool, str]:
        if profile.adapter != self.adapter_id:
            return False, "adapter_profile_mismatch"
        runtime = self._runtime or self._runtime_for(profile)
        if isinstance(runtime, HttpNeedleRuntime):
            return runtime.is_available(profile)
        return runtime.is_available()

    def propose(self, request: AdapterRequest) -> AdapterResult:
        started = self._clock()
        projection = self._dialects.project(request.tools, dialect="needle")
        runtime = self._runtime or self._runtime_for(request.profile)
        payload = runtime.complete(
            prompt=request.prompt, tools=[dict(item) for item in projection.tools],
            profile=request.profile, timeout_ms=request.timeout_ms,
        )
        return AdapterResult(
            "candidate", payload, "adapter_completed",
            (self._clock() - started) * 1000.0,
        )

    @staticmethod
    def _runtime_for(
        profile: TinyActionModelProfile,
    ) -> CactusNeedleRuntime | HttpNeedleRuntime:
        if profile.metadata.get("endpoint_env"):
            return HttpNeedleRuntime()
        return CactusNeedleRuntime()
