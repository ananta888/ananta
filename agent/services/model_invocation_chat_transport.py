"""Single provider chat call of ModelInvocationService: provider middleware,
prompt trace, cancellation fence and the HTTP exchange for one attempt.

``ChatTransport`` receives its wire codec, response-contract error
projection, provider middleware accessor, HTTP post function, cancellation
probe and LM Studio lock explicitly; nothing is looked up on a host class.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, NoReturn, Protocol

import requests

from agent.services.model_invocation_errors import LLMUnavailableError
from agent.services.model_invocation_payload_helpers import (
    fallback_error_type,
    finalize_trace_error,
    messages_for_tool_mode,
    tool_calling_mode,
)
from agent.services.model_invocation_profile import (
    build_llm_call_profile_entry,
)
from agent.services.model_invocation_response_policy import (
    ResponsePolicyFailureProjector,
    apply_local_response_policy,
)
from agent.services.model_invocation_support import (
    current_invocation_cancelled,
    provider_response_too_large,
    raise_llm_error,
    validate_local_runtime_payload,
)

# Shares the historical logger channel so log routing/filters stay unchanged.
logger = logging.getLogger("agent.services.model_invocation_service")

# LM Studio handles one inference at a time. Concurrent requests return empty content
# because the second request is queued/dropped. This lock serializes all LM Studio calls
# across threads (Flask runs with threaded=True, so planning and propose can overlap).
_LMSTUDIO_INFERENCE_LOCK = threading.Lock()


def post_with_requests(url: str, **kwargs: Any) -> Any:
    """Production HTTP post; ``requests.post`` is resolved per call."""

    return requests.post(url, **kwargs)


def default_provider_middleware() -> Any:
    from agent.services.provider_invocation_middleware import get_provider_invocation_middleware

    return get_provider_invocation_middleware()


class ProviderWireCoding(Protocol):
    """The wire-format operations the transport needs."""

    def request_body(
        self,
        *,
        provider: str,
        url: str,
        model: str,
        messages: list[dict],
        profile: Any,
        provider_context: Any,
        tools: list | None,
        send_native_tools: bool,
        response_format: dict | None,
    ) -> tuple[dict[str, Any], bool]: ...

    def normalize_response(
        self,
        payload: Any,
        *,
        ollama_generate: bool,
        ollama_chat: bool,
        model: str,
    ) -> Any: ...

    def response_redirect_denied(self, *, provider: str, request_url: str, response: Any) -> bool: ...


class ResponseContractErrorRaising(Protocol):
    def raise_contract_error(self, payload: dict[str, Any], *, error_type: str, detail: str) -> NoReturn: ...


class SingleChatCallTransport(Protocol):
    """One provider attempt; the chat pipeline depends only on this."""

    def make_single_chat_call(
        self,
        messages: list[dict],
        *,
        tools: list | None,
        response_format: dict | None,
        response_validator: Callable[[dict[str, Any]], None] | None = None,
        attempt: dict[str, Any],
        resolution_info: dict[str, Any],
        provider_context: Any = None,
    ) -> dict: ...


class ChatTransport:
    """Execute exactly one chat-completions attempt against one provider."""

    def __init__(
        self,
        *,
        codec: ProviderWireCoding,
        contract: ResponseContractErrorRaising,
        middleware_provider: Callable[[], Any] = default_provider_middleware,
        http_post: Callable[..., Any] = post_with_requests,
        cancellation_probe: Callable[[], bool] = current_invocation_cancelled,
        lmstudio_inference_lock: Any = None,
    ) -> None:
        self._codec = codec
        self._contract = contract
        self._middleware = middleware_provider
        self._http_post = http_post
        self._cancelled = cancellation_probe
        self._lmstudio_lock = (
            _LMSTUDIO_INFERENCE_LOCK if lmstudio_inference_lock is None else lmstudio_inference_lock
        )

    def _enforce_provider_response_limit(
        self,
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
        if not provider_response_too_large(response):
            return
        middleware.fail(
            prepared,
            provider=provider,
            model=model,
            reason_code="provider_response_too_large",
        )
        finalize_trace_error(
            prompt_trace,
            trace_service,
            "provider_response_too_large",
            "provider_response_too_large",
        )
        raise_llm_error(
            message="llm_provider_response_too_large",
            name="chat_completions",
            backend="llm_api",
            provider=provider,
            model=model,
            started_at=started_at,
            error_type="provider_response_too_large",
        )

    def make_single_chat_call(
        self,
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
        tool_mode = tool_calling_mode(profile)
        outgoing_messages, send_native_tools = messages_for_tool_mode(
            messages,
            tools=tools,
            tool_calling_mode=tool_mode,
        )
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body, ollama_generate = self._codec.request_body(
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
        validate_local_runtime_payload(provider=provider, payload=body)
        self._raise_if_cancelled_before_call(provider=provider, model=effective_model)

        middleware = self._middleware()
        prepared = self._prepare_with_middleware(
            middleware, provider_context=provider_context, provider=provider, model=effective_model, url=url, body=body
        )
        body = prepared.payload
        if prepared.cached_response is not None:
            return self._cached_response(
                prepared,
                provider=provider,
                model=effective_model,
                profile=profile,
                tools=tools,
                response_validator=response_validator,
            )

        prompt_trace, trace_svc = _start_prompt_trace(
            provider=provider, model=effective_model, body=body, send_native_tools=send_native_tools
        )
        started_at = time.time()
        lock = self._lmstudio_lock if provider in ("lmstudio", "lm_studio") else None
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
        post = http_session.post if http_session is not None else self._http_post
        call = _ChatCallFailure(
            middleware=middleware,
            prepared=prepared,
            provider=provider,
            model=effective_model,
            prompt_trace=prompt_trace,
            trace_service=trace_svc,
            started_at=started_at,
        )
        try:
            resp = _post_chat_request(post, url=url, body=body, headers=headers, timeout=timeout, call=call)
            self._check_http_response(resp, url=url, call=call)
            payload = self._decode_response_payload(
                resp,
                call=call,
                url=url,
                ollama_generate=ollama_generate,
                profile=profile,
                tools=tools,
            )
            _require_message_content(payload, call=call)
            usage = payload.get("usage") if isinstance(payload, dict) else {}
            _attach_call_profile(
                payload,
                call=call,
                usage=usage,
                profile=profile,
                tool_mode=tool_mode,
                resolution_info=resolution_info,
            )
            if response_validator is not None:
                _run_response_validator(response_validator, payload, call=call)
            middleware_result = middleware.complete(
                prepared,
                provider=provider,
                model=effective_model,
                response=payload if isinstance(payload, dict) else {},
            )
            _finalize_prompt_trace_success(prompt_trace, trace_svc, payload=payload, usage=usage)
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

    def _raise_if_cancelled_before_call(self, *, provider: str, model: str) -> None:
        if self._cancelled():
            raise_llm_error(
                message="llm_invocation_cancelled",
                name="chat_completions",
                backend="request_cancellation_fence",
                provider=provider,
                model=model,
                started_at=time.time(),
                error_type="cancelled",
            )

    @staticmethod
    def _prepare_with_middleware(
        middleware: Any, *, provider_context: Any, provider: str, model: str, url: str, body: dict[str, Any]
    ) -> Any:
        try:
            return middleware.prepare(
                context=provider_context,
                provider=provider,
                model=model,
                endpoint_url=url,
                payload=body,
            )
        except Exception as exc:
            from agent.services.provider_invocation_middleware import ProviderInvocationBlocked

            if not isinstance(exc, ProviderInvocationBlocked):
                raise
            raise_llm_error(
                message=exc.reason_code,
                name="chat_completions",
                backend="provider_middleware",
                provider=provider,
                model=model,
                started_at=time.time(),
                error_type=exc.reason_code,
            )

    def _cached_response(
        self,
        prepared: Any,
        *,
        provider: str,
        model: str,
        profile: Any,
        tools: list | None,
        response_validator: Callable[[dict[str, Any]], None] | None,
    ) -> dict:
        self._raise_if_cancelled_before_call(provider=provider, model=model)
        cached_payload = dict(prepared.cached_response)
        cached_meta = dict(cached_payload.get("metadata")) if isinstance(cached_payload.get("metadata"), dict) else {}
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
            raise_contract_error=self._contract.raise_contract_error,
        )
        if response_validator is not None:
            response_validator(cached_payload)
        return cached_payload

    def _check_http_response(self, resp: Any, *, url: str, call: _ChatCallFailure) -> None:
        """Cancellation fence, redirect, size and HTTP status checks after the exchange."""
        if self._cancelled():
            call.fail(
                "cancelled",
                detail="llm_invocation_cancelled",
                message="llm_invocation_cancelled",
                backend="request_cancellation_fence",
            )
        if self._codec.response_redirect_denied(
            provider=call.provider,
            request_url=url,
            response=resp,
        ):
            call.fail(
                "provider_redirect_denied",
                detail=f"HTTP {resp.status_code}",
                message=(f"llm_provider_redirect_denied: HTTP {resp.status_code}"),
            )
        self._enforce_provider_response_limit(
            response=resp,
            middleware=call.middleware,
            prepared=call.prepared,
            provider=call.provider,
            model=call.model,
            prompt_trace=call.prompt_trace,
            trace_service=call.trace_service,
            started_at=call.started_at,
        )
        if resp.status_code >= 500:
            call.fail(
                "server_error",
                detail=f"HTTP {resp.status_code}",
                message=f"llm_server_error: HTTP {resp.status_code}",
            )
        if resp.status_code >= 400:
            _fail_client_error(resp, call=call)

    def _decode_response_payload(
        self, resp: Any, *, call: _ChatCallFailure, url: str, ollama_generate: bool, profile: Any, tools: list | None
    ) -> Any:
        try:
            payload = resp.json()
        except Exception:
            call.fail("invalid_json_response", detail="invalid_json_response", message="llm_invalid_json_response")
        payload = self._codec.normalize_response(
            payload,
            ollama_generate=ollama_generate,
            ollama_chat=(call.provider == "ollama" and str(url).rstrip("/").endswith("/api/chat")),
            model=call.model,
        )
        return apply_local_response_policy(
            payload if isinstance(payload, dict) else {},
            profile=profile,
            tools_requested=bool(tools),
            on_failure=ResponsePolicyFailureProjector(
                middleware=call.middleware,
                prepared=call.prepared,
                provider=call.provider,
                model=call.model,
                prompt_trace=call.prompt_trace,
                trace_service=call.trace_service,
                finalize_trace_error=finalize_trace_error,
            ),
            raise_contract_error=self._contract.raise_contract_error,
        )


_CONTEXT_TOO_LARGE_MARKERS = (
    "context length",
    "context window",
    "too many tokens",
    "maximum context",
    "num_ctx",
)


class _ChatCallFailure:
    """Fail one in-flight chat attempt: middleware failure, trace error, then the LLM error."""

    def __init__(
        self,
        *,
        middleware: Any,
        prepared: Any,
        provider: str,
        model: str,
        prompt_trace: Any,
        trace_service: Any,
        started_at: float,
    ) -> None:
        self.middleware = middleware
        self.prepared = prepared
        self.provider = provider
        self.model = model
        self.prompt_trace = prompt_trace
        self.trace_service = trace_service
        self.started_at = started_at

    def fail(self, reason_code: str, *, detail: str, message: str, backend: str = "llm_api") -> NoReturn:
        self.middleware.fail(
            self.prepared,
            provider=self.provider,
            model=self.model,
            reason_code=reason_code,
        )
        finalize_trace_error(self.prompt_trace, self.trace_service, reason_code, detail)
        raise_llm_error(
            message=message,
            name="chat_completions",
            backend=backend,
            provider=self.provider,
            model=self.model,
            started_at=self.started_at,
            error_type=reason_code,
        )


def _start_prompt_trace(*, provider: str, model: str, body: dict[str, Any], send_native_tools: bool) -> tuple[Any, Any]:
    try:
        from flask import g, has_app_context

        if not has_app_context():
            return None, None
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
            model=model,
            endpoint_kind="chat_completions",
            request_kind="propose",
            messages=[message for message in list(body.get("messages") or []) if isinstance(message, dict)],
            tools=(list(body.get("tools") or []) if send_native_tools else []),
            llm_scope="task",
            sensitivity_level="internal",
        )
        return prompt_trace, trace_svc
    except Exception:
        return None, None


def _post_chat_request(
    post: Callable[..., Any],
    *,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    call: _ChatCallFailure,
) -> Any:
    try:
        return post(
            url,
            json=body,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.exceptions.ConnectionError:
        call.fail("connection_error", detail="provider_connection_failed", message="llm_connection_failed")
    except requests.exceptions.Timeout:
        call.fail("timeout", detail="provider_timeout", message="llm_timeout")


def _fail_client_error(resp: Any, *, call: _ChatCallFailure) -> NoReturn:
    response_excerpt = str(resp.text or "")[:200]
    normalized_response = response_excerpt.lower()
    error_type = (
        "context_too_large"
        if any(marker in normalized_response for marker in _CONTEXT_TOO_LARGE_MARKERS)
        else "client_error"
    )
    # the server's reason (e.g. an unsupported request field) keeps a 4xx diagnosable
    reason_excerpt = " ".join(response_excerpt.split())[:160]
    call.fail(
        error_type,
        detail=f"HTTP {resp.status_code}",
        message=f"llm_{error_type}: HTTP {resp.status_code}" + (f": {reason_excerpt}" if reason_excerpt else ""),
    )


def _require_message_content(payload: Any, *, call: _ChatCallFailure) -> None:
    first_choice = (payload.get("choices") or [{}])[0] if isinstance(payload, dict) else {}
    first_message = first_choice.get("message") if isinstance(first_choice, dict) else {}
    has_content = bool(str((first_message or {}).get("content") or "").strip())
    has_tool_calls = bool((first_message or {}).get("tool_calls"))
    if not has_content and not has_tool_calls:
        call.fail("empty_content", detail="LLM response has no content", message="llm_empty_content")


def _attach_call_profile(
    payload: Any,
    *,
    call: _ChatCallFailure,
    usage: Any,
    profile: Any,
    tool_mode: Any,
    resolution_info: dict[str, Any],
) -> None:
    ended_at = time.time()
    call_entry = build_llm_call_profile_entry(
        name="chat_completions",
        backend="llm_api",
        provider=call.provider,
        model=call.model,
        success=True,
        started_at=call.started_at,
        ended_at=ended_at,
        usage=usage if isinstance(usage, dict) else None,
    )
    call_entry["profile_id"] = getattr(profile, "profile_id", None)
    call_entry["tool_calling_mode"] = tool_mode
    if not isinstance(payload, dict):
        return
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if call.prompt_trace is not None:
        meta["prompt_trace_id"] = str(getattr(call.prompt_trace, "trace_id", "") or "")
    meta["llm_call_profile"] = list(meta.get("llm_call_profile") or []) + [call_entry]
    if resolution_info:
        meta["resolution_info"] = dict(resolution_info)
    payload["metadata"] = meta


def _run_response_validator(
    response_validator: Callable[[dict[str, Any]], None], payload: Any, *, call: _ChatCallFailure
) -> None:
    try:
        response_validator(payload)
    except LLMUnavailableError as exc:
        error_type = fallback_error_type(exc)
        call.middleware.fail(
            call.prepared,
            provider=call.provider,
            model=call.model,
            reason_code=error_type,
        )
        finalize_trace_error(
            call.prompt_trace,
            call.trace_service,
            error_type,
            str(exc),
        )
        raise


def _finalize_prompt_trace_success(prompt_trace: Any, trace_svc: Any, *, payload: Any, usage: Any) -> None:
    if prompt_trace is None or trace_svc is None:
        return
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
