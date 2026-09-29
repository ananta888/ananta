"""Shared helpers of ModelInvocationService: call-profile/error building,
observation hooks, cancellation fence and provider response limits."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from agent.services.local_runtime_request_policy import LocalRuntimeRequestPolicy
from agent.services.model_invocation_errors import LLMUnavailableError
from agent.services.model_invocation_observation_helpers import (
    observe_model_invocation_attempt,
)
from agent.services.model_invocation_payload_helpers import (
    blocked_candidates_as_dict,
    fallback_error_type,
    finalize_trace_error,
    max_output_tokens_for_request,
    messages_for_tool_mode,
    normalize_openai_tools,
    response_message,
    tool_calling_mode,
)
from agent.services.model_invocation_profile import (
    build_llm_call_profile_entry,
)


def composed_model_invocation_service() -> Any:
    """Return the composed ModelInvocationService class, looked up at call time.

    Static helpers of the mixins historically dispatched through
    ``ModelInvocationService.<name>``; resolving the class lazily keeps
    class-level overrides effective without a module-level import cycle.
    """

    from agent.services.model_invocation_service import ModelInvocationService

    return ModelInvocationService


class ModelInvocationSupportMixin:
    """Helper primitives shared by every ModelInvocationService responsibility."""

    _build_llm_call_profile_entry = staticmethod(build_llm_call_profile_entry)

    _observe_model_invocation_attempt = staticmethod(observe_model_invocation_attempt)

    @classmethod
    def _observe_successful_model_invocation_attempt(
        cls,
        *,
        payload: Any,
        attempt: Mapping[str, Any],
        resolution_info: Mapping[str, Any],
    ) -> None:
        profiles = (
            payload.get("metadata", {}).get("llm_call_profile", [])
            if isinstance(payload, dict) and isinstance(payload.get("metadata"), dict)
            else []
        )
        cls._observe_model_invocation_attempt(
            attempt=attempt,
            resolution_info=resolution_info,
            success=True,
            reason_code="invocation_completed",
            call_profile=(profiles[-1] if profiles and isinstance(profiles[-1], dict) else None),
        )

    @classmethod
    def _observe_failed_model_invocation_attempt(
        cls,
        *,
        error: LLMUnavailableError,
        error_type: str,
        attempt: Mapping[str, Any],
        resolution_info: Mapping[str, Any],
    ) -> None:
        profiles = error.llm_call_profile or []
        cls._observe_model_invocation_attempt(
            attempt=attempt,
            resolution_info=resolution_info,
            success=False,
            reason_code=error_type,
            call_profile=(profiles[-1] if profiles and isinstance(profiles[-1], dict) else None),
        )

    @staticmethod
    def _decorate_invocation_payload(
        payload: Any,
        *,
        call_profile: list[dict[str, Any]],
        fallback_decisions: list[dict[str, Any]],
        resolution_info: Mapping[str, Any],
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        metadata = payload.get("metadata")
        meta = dict(metadata) if isinstance(metadata, dict) else {}
        meta["llm_call_profile"] = call_profile + list(meta.get("llm_call_profile") or [])
        meta["fallback_decisions"] = list(fallback_decisions)
        if resolution_info:
            meta["resolution_info"] = dict(resolution_info)
        payload["metadata"] = meta
        return payload

    @classmethod
    def _raise_llm_error(
        cls,
        *,
        message: str,
        name: str,
        backend: str,
        provider: str | None,
        model: str | None,
        started_at: float | None,
        error_type: str,
    ) -> None:
        ended_at = time.time()
        entry = cls._build_llm_call_profile_entry(
            name=name,
            backend=backend,
            provider=provider,
            model=model,
            success=False,
            started_at=started_at,
            ended_at=ended_at,
            usage=None,
            source="model_invocation_service",
            estimated=False,
            error_type=error_type,
            error_message=message,
        )
        raise LLMUnavailableError(
            message,
            llm_call_profile=[entry],
            terminal_reason=error_type,
        )

    @staticmethod
    def _current_invocation_cancelled() -> bool:
        """Read the existing Hub/Worker request fence without owning it."""

        try:
            from agent.services.lmstudio_request_registry import (
                _get_current_context,
                is_cancelled,
            )

            return is_cancelled(*_get_current_context())
        except Exception:
            return False

    @staticmethod
    def _provider_response_too_large(response: Any, *, maximum_bytes: int = 2 * 1024 * 1024) -> bool:
        headers = getattr(response, "headers", None)
        if isinstance(headers, Mapping):
            declared = headers.get("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > maximum_bytes:
                        return True
                except (TypeError, ValueError):
                    return True
        content = getattr(response, "content", None)
        return isinstance(content, (bytes, bytearray)) and len(content) > maximum_bytes

    @staticmethod
    def _validate_local_runtime_payload(*, provider: str, payload: Mapping[str, Any]) -> None:
        if provider in {"ollama", "lmstudio", "lm_studio"}:
            LocalRuntimeRequestPolicy().validate_payload(payload)

    @classmethod
    def _enforce_provider_response_limit(
        cls,
        *,
        response: Any,
        middleware: Any,
        prepared: Any,
        provider: str,
        model: str,
        prompt_trace: Any,
        trace_service: Any,
        started_at: float,
    ) -> None:
        if not cls._provider_response_too_large(response):
            return
        middleware.fail(
            prepared,
            provider=provider,
            model=model,
            reason_code="provider_response_too_large",
        )
        cls._finalize_trace_error(
            prompt_trace,
            trace_service,
            "provider_response_too_large",
            "provider_response_too_large",
        )
        cls._raise_llm_error(
            message="llm_provider_response_too_large",
            name="chat_completions",
            backend="llm_api",
            provider=provider,
            model=model,
            started_at=started_at,
            error_type="provider_response_too_large",
        )

    _normalize_openai_tools = staticmethod(normalize_openai_tools)
    _tool_calling_mode = staticmethod(tool_calling_mode)
    _max_output_tokens_for_request = staticmethod(max_output_tokens_for_request)
    _messages_for_tool_mode = staticmethod(messages_for_tool_mode)
    _blocked_candidates_as_dict = staticmethod(blocked_candidates_as_dict)
    _fallback_error_type = staticmethod(fallback_error_type)
    _finalize_trace_error = staticmethod(finalize_trace_error)
    _response_message = staticmethod(response_message)
