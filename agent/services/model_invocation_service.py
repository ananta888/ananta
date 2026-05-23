"""ModelInvocationService — real LLM HTTP calls for propose strategies. FA-T021."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# LM Studio handles one inference at a time. Concurrent requests return empty content
# because the second request is queued/dropped. This lock serializes all LM Studio calls
# across threads (Flask runs with threaded=True, so planning and propose can overlap).
_LMSTUDIO_INFERENCE_LOCK = threading.Lock()


class LLMUnavailableError(Exception):
    """LLM provider not reachable, timed out, or returned server error."""

    def __init__(self, message: str, *, llm_call_profile: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.llm_call_profile = list(llm_call_profile or [])


class ModelInvocationService:
    """LLM invocation via OpenAI-compatible chat/completions endpoint."""

    @staticmethod
    def _build_llm_call_profile_entry(
        *,
        name: str,
        backend: str,
        provider: str | None,
        model: str | None,
        success: bool,
        started_at: float | None,
        ended_at: float | None,
        usage: dict[str, Any] | None = None,
        source: str = "model_invocation_service",
        estimated: bool = False,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        usage = usage if isinstance(usage, dict) else {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        latency_ms = None
        if started_at is not None and ended_at is not None:
            latency_ms = max(0, int((ended_at - started_at) * 1000))
        return {
            "name": name,
            "backend": backend,
            "provider": str(provider or "").strip() or None,
            "model": str(model or "").strip() or None,
            "success": bool(success),
            "latency_ms": latency_ms,
            "prompt_tokens": int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
            "completion_tokens": int(completion_tokens) if isinstance(completion_tokens, int) else None,
            "total_tokens": int(total_tokens) if isinstance(total_tokens, int) else None,
            "source": source,
            "estimated": bool(estimated),
            "error_type": str(error_type or "").strip() or None,
            "error_message": str(error_message or "").strip() or None,
            "started_at": float(started_at) if started_at is not None else None,
            "ended_at": float(ended_at) if ended_at is not None else None,
        }

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
        raise LLMUnavailableError(message, llm_call_profile=[entry])

    @classmethod
    def _get_settings(cls):
        from agent.config import settings
        return settings

    @classmethod
    def _provider_info(cls) -> tuple[str, str, str | None]:
        """Return (provider_label, chat_completions_url, api_key)."""
        s = cls._get_settings()
        provider = (s.default_provider or "lmstudio").strip().lower()

        if provider in ("lmstudio", "lm_studio"):
            base = s.lmstudio_url.rstrip("/")
            # lmstudio_url may point to /v1 base or /v1/chat/completions
            if not base.endswith("/chat/completions"):
                base = base + "/chat/completions"
            return "lmstudio", base, None

        if provider == "ollama":
            base = s.ollama_url.rstrip("/")
            # ollama_url defaults to /api/generate; use OpenAI-compat endpoint
            if "/api/generate" in base:
                base = base.replace("/api/generate", "")
            if not base.endswith("/chat/completions"):
                if not base.endswith("/v1"):
                    base = base + "/v1"
                base = base + "/chat/completions"
            return "ollama", base, None

        if provider == "openai":
            url = s.openai_url.rstrip("/")
            if not url.endswith("/chat/completions"):
                url = url + "/chat/completions"
            return "openai", url, s.openai_api_key

        if provider == "mock":
            return "mock", s.mock_url.rstrip("/") + "/v1/chat/completions", None

        # Generic OpenAI-compatible fallback
        base = s.lmstudio_url.rstrip("/")
        if not base.endswith("/chat/completions"):
            base = base + "/chat/completions"
        return provider, base, None

    @staticmethod
    def _normalize_openai_tools(tools: list | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in list(tools or []):
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "function" and isinstance(item.get("function"), dict):
                normalized.append(item)
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                fn = item.get("function") if isinstance(item.get("function"), dict) else {}
                name = str(fn.get("name") or "").strip()
            if not name:
                continue
            description = str(item.get("description") or "").strip()
            if not description:
                fn = item.get("function") if isinstance(item.get("function"), dict) else {}
                description = str(fn.get("description") or "").strip()
            parameters = item.get("parameters")
            if not isinstance(parameters, dict):
                fn = item.get("function") if isinstance(item.get("function"), dict) else {}
                parameters = fn.get("parameters")
            if not isinstance(parameters, dict):
                parameters = {"type": "object", "properties": {}}
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": parameters,
                    },
                }
            )
        return normalized

    @classmethod
    def _make_chat_call(
        cls,
        messages: list[dict],
        *,
        tools: list | None = None,
        response_format: dict | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> dict:
        """POST chat/completions and return parsed JSON response."""
        provider, url, api_key = cls._provider_info()
        s = cls._get_settings()

        effective_model = model
        if not effective_model or effective_model == "auto":
            effective_model = s.default_model

        if timeout is None:
            timeout = int(getattr(s, "llm_invoke_timeout_seconds", None) or 120)

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body: dict[str, Any] = {"model": effective_model, "messages": messages}
        if tools:
            body["tools"] = cls._normalize_openai_tools(tools)
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format

        logger.debug("LLM call provider=%s url=%s model=%s tools=%s timeout=%s", provider, url, effective_model, bool(tools), timeout)

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
                    messages=[m for m in list(messages or []) if isinstance(m, dict)],
                    tools=cls._normalize_openai_tools(tools),
                    llm_scope="task",
                    sensitivity_level="internal",
                )
        except Exception:
            prompt_trace = None
            trace_svc = None

        started_at = time.time()
        _lmstudio_lock = _LMSTUDIO_INFERENCE_LOCK if provider in ("lmstudio", "lm_studio") else None
        if _lmstudio_lock is not None:
            if not _lmstudio_lock.acquire(blocking=False):
                logger.debug("LM Studio busy — waiting for inference lock (provider=%s)", provider)
                _lmstudio_lock.acquire()
        try:
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=timeout)
            except requests.exceptions.ConnectionError as exc:
                if prompt_trace is not None and trace_svc is not None:
                    try:
                        finalized = trace_svc.finalize_trace(
                            prompt_trace,
                            success=False,
                            error_type="connection_error",
                            error_message=f"{exc}",
                        )
                        trace_svc.store(finalized)
                    except Exception:
                        pass
                cls._raise_llm_error(
                    message=f"llm_connection_failed: {exc}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="connection_error",
                )
            except requests.exceptions.Timeout as exc:
                if prompt_trace is not None and trace_svc is not None:
                    try:
                        finalized = trace_svc.finalize_trace(
                            prompt_trace,
                            success=False,
                            error_type="timeout",
                            error_message=f"{exc}",
                        )
                        trace_svc.store(finalized)
                    except Exception:
                        pass
                cls._raise_llm_error(
                    message=f"llm_timeout: {exc}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="timeout",
                )

            if resp.status_code >= 500:
                if prompt_trace is not None and trace_svc is not None:
                    try:
                        finalized = trace_svc.finalize_trace(
                            prompt_trace,
                            success=False,
                            error_type="server_error",
                            error_message=f"HTTP {resp.status_code}",
                        )
                        trace_svc.store(finalized)
                    except Exception:
                        pass
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
                if prompt_trace is not None and trace_svc is not None:
                    try:
                        finalized = trace_svc.finalize_trace(
                            prompt_trace,
                            success=False,
                            error_type="client_error",
                            error_message=f"HTTP {resp.status_code} {resp.text[:200]}",
                        )
                        trace_svc.store(finalized)
                    except Exception:
                        pass
                cls._raise_llm_error(
                    message=f"llm_client_error: HTTP {resp.status_code} {resp.text[:200]}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="client_error",
                )

            try:
                payload = resp.json()
                ended_at = time.time()
                usage = payload.get("usage") if isinstance(payload, dict) else {}
                if prompt_trace is not None and trace_svc is not None:
                    try:
                        msg_content = ""
                        if isinstance(payload, dict):
                            first = ((payload.get("choices") or [{}])[0] or {})
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
                profile = cls._build_llm_call_profile_entry(
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    success=True,
                    started_at=started_at,
                    ended_at=ended_at,
                    usage=usage if isinstance(usage, dict) else None,
                )
                if isinstance(payload, dict):
                    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                    if prompt_trace is not None:
                        meta["prompt_trace_id"] = str(getattr(prompt_trace, "trace_id", "") or "")
                    meta["llm_call_profile"] = list(meta.get("llm_call_profile") or []) + [profile]
                    payload["metadata"] = meta
                return payload
            except Exception as exc:
                if prompt_trace is not None and trace_svc is not None:
                    try:
                        finalized = trace_svc.finalize_trace(
                            prompt_trace,
                            success=False,
                            error_type="invalid_json_response",
                            error_message=f"{exc}",
                        )
                        trace_svc.store(finalized)
                    except Exception:
                        pass
                cls._raise_llm_error(
                    message=f"llm_invalid_json_response: {exc}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="invalid_json_response",
                )
        finally:
            if _lmstudio_lock is not None:
                _lmstudio_lock.release()

    @classmethod
    def invoke_with_tools(
        cls, prompt: str, tools: list, model: str | None = None, **kwargs
    ) -> dict:
        """Call LLM with tools= parameter. Returns dict with tool_calls list and content."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages

        response = cls._make_chat_call(
            messages,
            tools=tools,
            model=model,
            timeout=kwargs.get("timeout"),
        )
        choice = (response.get("choices") or [{}])[0]
        msg = choice.get("message") or {}

        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments", "{}")
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                parsed_args = {"raw": raw_args}
            tool_calls.append({
                "name": fn.get("name", ""),
                "args": parsed_args,
                "id": tc.get("id"),
            })

        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        return {
            "tool_calls": tool_calls,
            "content": msg.get("content") or "",
            "finish_reason": choice.get("finish_reason"),
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            "metadata": metadata,
            "provider": cls._provider_info()[0],
            "model": response.get("model") or model,
        }

    @classmethod
    def invoke_with_json_schema(
        cls, prompt: str, json_schema: dict, model: str | None = None, **kwargs
    ) -> str:
        """Call LLM with response_format=json_object. Returns raw content string."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(
            messages, response_format={"type": "json_object"}, model=model
        )
        choice = (response.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or ""

    @classmethod
    def invoke_with_json_schema_result(
        cls, prompt: str, json_schema: dict, model: str | None = None, **kwargs
    ) -> dict[str, Any]:
        """Call LLM with response_format=json_object and keep metadata/usage."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(
            messages,
            response_format={"type": "json_object"},
            model=model,
            timeout=kwargs.get("timeout"),
        )
        choice = (response.get("choices") or [{}])[0]
        msg = choice.get("message") if isinstance(choice, dict) else {}
        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        provider = cls._provider_info()[0]
        return {
            "content": (msg.get("content") or "") if isinstance(msg, dict) else "",
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            "metadata": metadata,
            "provider": provider,
            "model": response.get("model") or model,
        }

    @classmethod
    def invoke(cls, prompt: str, model: str | None = None, **kwargs) -> str:
        """Plain chat completion. Returns content string."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(messages, model=model)
        choice = (response.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or ""

    @classmethod
    def invoke_result(cls, prompt: str, model: str | None = None, **kwargs) -> dict[str, Any]:
        """Plain chat completion with metadata/usage (additive API)."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(messages, model=model, timeout=kwargs.get("timeout"))
        choice = (response.get("choices") or [{}])[0]
        msg = choice.get("message") if isinstance(choice, dict) else {}
        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        provider = cls._provider_info()[0]
        return {
            "content": (msg.get("content") or "") if isinstance(msg, dict) else "",
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            "metadata": metadata,
            "provider": provider,
            "model": response.get("model") or model,
        }
