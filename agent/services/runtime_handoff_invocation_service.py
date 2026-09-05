"""Invoke one Hub-resolved runtime endpoint through an explicit provider port."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.model_invocation_errors import ModelRoutingConfigurationError


class BoundProviderInvocationPort(Protocol):
    def invoke(
        self,
        *,
        prompt: str,
        profile: Any,
        model_id: str,
        timeout_seconds: int,
        resolution_info: Mapping[str, Any],
    ) -> dict[str, Any]: ...


class ModelInvocationBoundProviderAdapter:
    """Narrow compatibility adapter around the existing invocation facade."""

    def invoke(
        self,
        *,
        prompt: str,
        profile: Any,
        model_id: str,
        timeout_seconds: int,
        resolution_info: Mapping[str, Any],
    ) -> dict[str, Any]:
        from agent.services.model_invocation_service import ModelInvocationService

        provider, url, api_key = ModelInvocationService._provider_info_from_profile(profile)
        return ModelInvocationService._make_single_chat_call(
            [{"role": "user", "content": prompt}],
            tools=None,
            response_format=None,
            attempt={
                "provider": provider,
                "url": url,
                "api_key": api_key,
                "model": model_id,
                "timeout": timeout_seconds,
                "profile": profile,
            },
            resolution_info=dict(resolution_info),
        )


class RuntimeHandoffInvocationService:
    """Enforce endpoint/profile bindings and prohibit implicit fallback."""

    def __init__(self, provider: BoundProviderInvocationPort | None = None) -> None:
        self._provider = provider or ModelInvocationBoundProviderAdapter()

    def invoke_result(
        self,
        prompt: str,
        *,
        endpoint: Mapping[str, Any],
        profile: Any,
    ) -> dict[str, Any]:
        provider_id = str(endpoint.get("provider_id") or "").strip().lower()
        model_id = str(endpoint.get("model_id") or "").strip()
        limits = endpoint.get("limits")
        capabilities = endpoint.get("api_capabilities")
        if (
            not str(prompt or "").strip()
            or endpoint.get("fallback") is not None
            or endpoint.get("required_capability") != "openai_chat"
            or not isinstance(capabilities, Mapping)
            or capabilities.get("openai_chat") is not True
            or not isinstance(limits, Mapping)
            or provider_id != str(getattr(profile, "provider_id", "") or "").strip().lower()
            or model_id != str(getattr(profile, "model", "") or "").strip()
            or int(limits.get("max_output_tokens") or 0)
            != int(getattr(profile, "max_output_tokens", 0) or 0)
            or int(limits.get("context_tokens") or 0)
            != int(getattr(profile, "context_tokens", 0) or 0)
        ):
            raise ModelRoutingConfigurationError("runtime_handoff_invocation_binding_invalid")
        return self._provider.invoke(
            prompt=prompt,
            profile=profile,
            model_id=model_id,
            timeout_seconds=int(limits.get("timeout_seconds") or 120),
            resolution_info={
                "resolution_source": "runtime_handoff",
                "endpoint_id": endpoint.get("endpoint_id"),
                "endpoint_revision": endpoint.get("endpoint_revision"),
            },
        )


__all__ = [
    "BoundProviderInvocationPort",
    "ModelInvocationBoundProviderAdapter",
    "RuntimeHandoffInvocationService",
]
