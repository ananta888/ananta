"""Shared primitives of ModelInvocationService collaborators.

Pure helpers for call-profile errors, payload decoration, the cancellation
fence and provider response limits, plus the attempt observer that the chat
pipeline receives explicitly. Nothing here looks the composed service up.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, NoReturn, Protocol

from agent.services.local_runtime_request_policy import LocalRuntimeRequestPolicy
from agent.services.model_invocation_errors import LLMUnavailableError
from agent.services.model_invocation_observation_helpers import (
    observe_model_invocation_attempt,
)
from agent.services.model_invocation_profile import (
    build_llm_call_profile_entry,
)

PROVIDER_RESPONSE_MAXIMUM_BYTES = 2 * 1024 * 1024


def raise_llm_error(
    *,
    message: str,
    name: str,
    backend: str,
    provider: str | None,
    model: str | None,
    started_at: float | None,
    error_type: str,
) -> NoReturn:
    """Raise ``LLMUnavailableError`` carrying one failed call-profile entry."""

    ended_at = time.time()
    entry = build_llm_call_profile_entry(
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


def decorate_invocation_payload(
    payload: Any,
    *,
    call_profile: list[dict[str, Any]],
    fallback_decisions: list[dict[str, Any]],
    resolution_info: Mapping[str, Any],
) -> Any:
    """Prefix earlier failed attempts and fallback decisions to a successful payload."""

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


def current_invocation_cancelled() -> bool:
    """Read the existing Hub/Worker request fence without owning it."""

    try:
        from agent.services.lmstudio_request_registry import (
            _get_current_context,
            is_cancelled,
        )

        return is_cancelled(*_get_current_context())
    except Exception:
        return False


def provider_response_too_large(
    response: Any,
    *,
    maximum_bytes: int = PROVIDER_RESPONSE_MAXIMUM_BYTES,
) -> bool:
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


def validate_local_runtime_payload(*, provider: str, payload: Mapping[str, Any]) -> None:
    if provider in {"ollama", "lmstudio", "lm_studio"}:
        LocalRuntimeRequestPolicy().validate_payload(payload)


class InvocationAttemptObserving(Protocol):
    """Receives the outcome of every provider attempt of the chat pipeline."""

    def observe_success(
        self,
        *,
        payload: Any,
        attempt: Mapping[str, Any],
        resolution_info: Mapping[str, Any],
    ) -> None: ...

    def observe_failure(
        self,
        *,
        error: LLMUnavailableError,
        error_type: str,
        attempt: Mapping[str, Any],
        resolution_info: Mapping[str, Any],
    ) -> None: ...


class ModelInvocationAttemptObserver:
    """Project attempt outcomes onto the content-free observation hook."""

    def __init__(
        self,
        observe: Callable[..., None] = observe_model_invocation_attempt,
    ) -> None:
        self._observe = observe

    def observe_success(
        self,
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
        self._observe(
            attempt=attempt,
            resolution_info=resolution_info,
            success=True,
            reason_code="invocation_completed",
            call_profile=(profiles[-1] if profiles and isinstance(profiles[-1], dict) else None),
        )

    def observe_failure(
        self,
        *,
        error: LLMUnavailableError,
        error_type: str,
        attempt: Mapping[str, Any],
        resolution_info: Mapping[str, Any],
    ) -> None:
        profiles = error.llm_call_profile or []
        self._observe(
            attempt=attempt,
            resolution_info=resolution_info,
            success=False,
            reason_code=error_type,
            call_profile=(profiles[-1] if profiles and isinstance(profiles[-1], dict) else None),
        )
