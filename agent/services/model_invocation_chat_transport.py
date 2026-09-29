"""Single provider chat call of ModelInvocationService: provider middleware,
prompt trace, cancellation fence and the HTTP exchange for one attempt."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import requests

from agent.services.model_invocation_errors import LLMUnavailableError
from agent.services.model_invocation_response_policy import (
    ResponsePolicyFailureProjector,
    apply_local_response_policy,
)

# Shares the historical logger channel so log routing/filters stay unchanged.
logger = logging.getLogger("agent.services.model_invocation_service")

# LM Studio handles one inference at a time. Concurrent requests return empty content
# because the second request is queued/dropped. This lock serializes all LM Studio calls
# across threads (Flask runs with threaded=True, so planning and propose can overlap).
_LMSTUDIO_INFERENCE_LOCK = threading.Lock()


class ModelInvocationChatTransportMixin:
    """Execute exactly one chat-completions attempt against one provider."""

    @classmethod
    def _make_single_chat_call(
        cls,
        messages: list[dict],
        *,
        tools: list | None,
        response_format: dict | None,
        response_validator: Callable[[dict[str, Any]], None] | None = None,
        attempt: dict[str, Any],
        resolution_info: dict[str, Any],
        provider_context: Any = None,
    ) -> dict:
        provider = attempt["provider"]
        url = attempt["url"]
        api_key = attempt.get("api_key")
        effective_model = attempt["model"]
        timeout = int(attempt.get("timeout") or 120)
        profile = attempt.get("profile")
        tool_mode = cls._tool_calling_mode(profile)
        outgoing_messages, send_native_tools = cls._messages_for_tool_mode(
            messages,
            tools=tools,
            tool_calling_mode=tool_mode,
        )
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body, ollama_generate = cls._provider_request_body(
            provider=provider,
            url=url,
            model=effective_model,
            messages=outgoing_messages,
            profile=profile,
            provider_context=provider_context,
            tools=tools,
            send_native_tools=send_native_tools,
            response_format=response_format,
        )
        cls._validate_local_runtime_payload(provider=provider, payload=body)

        if cls._current_invocation_cancelled():
            cls._raise_llm_error(
                message="llm_invocation_cancelled",
                name="chat_completions",
                backend="request_cancellation_fence",
                provider=provider,
                model=effective_model,
                started_at=time.time(),
                error_type="cancelled",
            )

        middleware = cls._get_provider_middleware()
        try:
            prepared = middleware.prepare(
                context=provider_context,
                provider=provider,
                model=effective_model,
                endpoint_url=url,
                payload=body,
            )
        except Exception as exc:
            from agent.services.provider_invocation_middleware import ProviderInvocationBlocked

            if not isinstance(exc, ProviderInvocationBlocked):
                raise
            cls._raise_llm_error(
                message=exc.reason_code,
                name="chat_completions",
                backend="provider_middleware",
                provider=provider,
                model=effective_model,
                started_at=time.time(),
                error_type=exc.reason_code,
            )
        body = prepared.payload
        if prepared.cached_response is not None:
            if cls._current_invocation_cancelled():
                cls._raise_llm_error(
                    message="llm_invocation_cancelled",
                    name="chat_completions",
                    backend="request_cancellation_fence",
                    provider=provider,
                    model=effective_model,
                    started_at=time.time(),
                    error_type="cancelled",
                )
            cached_payload = dict(prepared.cached_response)
            cached_meta = (
                dict(cached_payload.get("metadata")) if isinstance(cached_payload.get("metadata"), dict) else {}
            )
            cached_meta["provider_middleware"] = {
                "schema": "ananta.provider_middleware_result.v1",
                "cache_key": prepared.cache_key,
                "payload_hash": prepared.payload_hash,
                "cache_hit": True,
            }
            cached_payload["metadata"] = cached_meta
            cached_payload = apply_local_response_policy(
                cached_payload,
                profile=profile,
                tools_requested=bool(tools),
                raise_contract_error=cls._raise_response_contract_error,
            )
            if response_validator is not None:
                response_validator(cached_payload)
            return cached_payload

        prompt_trace = None
        trace_svc = None
        try:
            from flask import g, has_app_context

            if has_app_context():
                from agent.services.prompt_trace_service import get_prompt_trace_service

                trace_goal_id = str(getattr(g, "llm_goal_id", "") or "").strip() or None
                trace_task_id = str(getattr(g, "llm_task_id", "") or "").strip() or None
                trace_svc = get_prompt_trace_service()
                prompt_trace = trace_svc.create_trace(
                    goal_id=trace_goal_id,
                    task_id=trace_task_id,
                    source_component="model_invocation_service",
                    provider=provider,
                    transport_provider=provider,
                    model=effective_model,
                    endpoint_kind="chat_completions",
                    request_kind="propose",
                    messages=[message for message in list(body.get("messages") or []) if isinstance(message, dict)],
                    tools=(list(body.get("tools") or []) if send_native_tools else []),
                    llm_scope="task",
                    sensitivity_level="internal",
                )
        except Exception:
            prompt_trace = None
            trace_svc = None

        started_at = time.time()
        lock = _LMSTUDIO_INFERENCE_LOCK if provider in ("lmstudio", "lm_studio") else None
        if lock is not None:
            if not lock.acquire(blocking=False):
                logger.debug("LM Studio busy - waiting for inference lock (provider=%s)", provider)
                lock.acquire()
        from agent.common.lmstudio_request_registry import (
            _get_current_context,
            create_and_register_session,
            release_session,
        )

        # A call made for a task/goal is registered under it: cancelling it (dispatch hard timeout, operator
        # cancel) shuts the connection down and the model server stops generating instead of blocking a slot.
        # Without a task/goal nothing can cancel it: the plain request as before.
        http_session, session_key = create_and_register_session() if any(_get_current_context()) else (None, None)
        post = http_session.post if http_session is not None else requests.post
        try:
            try:
                resp = post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=False,
                )
            except requests.exceptions.ConnectionError:
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="connection_error",
                )
                cls._finalize_trace_error(prompt_trace, trace_svc, "connection_error", "provider_connection_failed")
                cls._raise_llm_error(
                    message="llm_connection_failed",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="connection_error",
                )
            except requests.exceptions.Timeout:
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="timeout",
                )
                cls._finalize_trace_error(prompt_trace, trace_svc, "timeout", "provider_timeout")
                cls._raise_llm_error(
                    message="llm_timeout",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="timeout",
                )

            if cls._current_invocation_cancelled():
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="cancelled",
                )
                cls._finalize_trace_error(
                    prompt_trace,
                    trace_svc,
                    "cancelled",
                    "llm_invocation_cancelled",
                )
                cls._raise_llm_error(
                    message="llm_invocation_cancelled",
                    name="chat_completions",
                    backend="request_cancellation_fence",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="cancelled",
                )

            if cls._provider_response_redirect_denied(
                provider=provider,
                request_url=url,
                response=resp,
            ):
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="provider_redirect_denied",
                )
                cls._finalize_trace_error(
                    prompt_trace,
                    trace_svc,
                    "provider_redirect_denied",
                    f"HTTP {resp.status_code}",
                )
                cls._raise_llm_error(
                    message=(f"llm_provider_redirect_denied: HTTP {resp.status_code}"),
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="provider_redirect_denied",
                )
            cls._enforce_provider_response_limit(
                response=resp,
                middleware=middleware,
                prepared=prepared,
                provider=provider,
                model=effective_model,
                prompt_trace=prompt_trace,
                trace_service=trace_svc,
                started_at=started_at,
            )
            if resp.status_code >= 500:
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="server_error",
                )
                cls._finalize_trace_error(prompt_trace, trace_svc, "server_error", f"HTTP {resp.status_code}")
                cls._raise_llm_error(
                    message=f"llm_server_error: HTTP {resp.status_code}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="server_error",
                )
            if resp.status_code >= 400:
                response_excerpt = str(resp.text or "")[:200]
                normalized_response = response_excerpt.lower()
                error_type = (
                    "context_too_large"
                    if any(
                        marker in normalized_response
                        for marker in (
                            "context length",
                            "context window",
                            "too many tokens",
                            "maximum context",
                            "num_ctx",
                        )
                    )
                    else "client_error"
                )
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code=error_type,
                )
                cls._finalize_trace_error(
                    prompt_trace, trace_svc, error_type, f"HTTP {resp.status_code}"
                )
                # the server's reason (e.g. an unsupported request field) keeps a 4xx diagnosable
                reason_excerpt = " ".join(response_excerpt.split())[:160]
                cls._raise_llm_error(
                    message=f"llm_{error_type}: HTTP {resp.status_code}" + (f": {reason_excerpt}" if reason_excerpt else ""),
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type=error_type,
                )

            try:
                payload = resp.json()
            except Exception:
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="invalid_json_response",
                )
                cls._finalize_trace_error(
                    prompt_trace,
                    trace_svc,
                    "invalid_json_response",
                    "invalid_json_response",
                )
                cls._raise_llm_error(
                    message="llm_invalid_json_response",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="invalid_json_response",
                )
            payload = cls._normalize_provider_response(
                payload,
                ollama_generate=ollama_generate,
                ollama_chat=(
                    provider == "ollama"
                    and str(url).rstrip("/").endswith("/api/chat")
                ),
                model=effective_model,
            )
            payload = apply_local_response_policy(
                payload if isinstance(payload, dict) else {},
                profile=profile,
                tools_requested=bool(tools),
                on_failure=ResponsePolicyFailureProjector(
                    middleware=middleware,
                    prepared=prepared,
                    provider=provider,
                    model=effective_model,
                    prompt_trace=prompt_trace,
                    trace_service=trace_svc,
                    finalize_trace_error=cls._finalize_trace_error,
                ),
                raise_contract_error=cls._raise_response_contract_error,
            )

            first_choice = (payload.get("choices") or [{}])[0] if isinstance(payload, dict) else {}
            first_message = first_choice.get("message") if isinstance(first_choice, dict) else {}
            has_content = bool(str((first_message or {}).get("content") or "").strip())
            has_tool_calls = bool((first_message or {}).get("tool_calls"))
            if not has_content and not has_tool_calls:
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="empty_content",
                )
                cls._finalize_trace_error(prompt_trace, trace_svc, "empty_content", "LLM response has no content")
                cls._raise_llm_error(
                    message="llm_empty_content",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="empty_content",
                )

            ended_at = time.time()
            usage = payload.get("usage") if isinstance(payload, dict) else {}
            call_entry = cls._build_llm_call_profile_entry(
                name="chat_completions",
                backend="llm_api",
                provider=provider,
                model=effective_model,
                success=True,
                started_at=started_at,
                ended_at=ended_at,
                usage=usage if isinstance(usage, dict) else None,
            )
            call_entry["profile_id"] = getattr(profile, "profile_id", None)
            call_entry["tool_calling_mode"] = tool_mode
            if isinstance(payload, dict):
                meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                if prompt_trace is not None:
                    meta["prompt_trace_id"] = str(getattr(prompt_trace, "trace_id", "") or "")
                meta["llm_call_profile"] = list(meta.get("llm_call_profile") or []) + [call_entry]
                if resolution_info:
                    meta["resolution_info"] = dict(resolution_info)
                payload["metadata"] = meta
            if response_validator is not None:
                try:
                    response_validator(payload)
                except LLMUnavailableError as exc:
                    error_type = cls._fallback_error_type(exc)
                    middleware.fail(
                        prepared,
                        provider=provider,
                        model=effective_model,
                        reason_code=error_type,
                    )
                    cls._finalize_trace_error(
                        prompt_trace,
                        trace_svc,
                        error_type,
                        str(exc),
                    )
                    raise
            middleware_result = middleware.complete(
                prepared,
                provider=provider,
                model=effective_model,
                response=payload if isinstance(payload, dict) else {},
            )
            if prompt_trace is not None and trace_svc is not None:
                try:
                    msg_content = ""
                    if isinstance(payload, dict):
                        first = (payload.get("choices") or [{}])[0] or {}
                        msg = first.get("message") if isinstance(first, dict) else {}
                        msg_content = str((msg or {}).get("content") or "")
                    finalized = trace_svc.finalize_trace(
                        prompt_trace,
                        success=True,
                        response_text=msg_content or None,
                        usage=usage if isinstance(usage, dict) else None,
                    )
                    trace_svc.store(finalized)
                except Exception:
                    pass
            if isinstance(payload, dict):
                meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                meta["provider_middleware"] = middleware_result
                payload["metadata"] = meta
            return payload
        finally:
            if http_session is not None:
                release_session(session_key, http_session)
                http_session.close()
            if lock is not None:
                lock.release()
