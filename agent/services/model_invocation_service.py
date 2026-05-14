"""ModelInvocationService — real LLM HTTP calls for propose strategies. FA-T021."""
from __future__ import annotations

import json
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    """LLM provider not reachable, timed out, or returned server error."""


class ModelInvocationService:
    """LLM invocation via OpenAI-compatible chat/completions endpoint."""

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

    @classmethod
    def _make_chat_call(
        cls,
        messages: list[dict],
        *,
        tools: list | None = None,
        response_format: dict | None = None,
        model: str | None = None,
        timeout: int = 60,
    ) -> dict:
        """POST chat/completions and return parsed JSON response."""
        provider, url, api_key = cls._provider_info()
        s = cls._get_settings()

        effective_model = model
        if not effective_model or effective_model == "auto":
            default = s.default_model
            effective_model = default if (default and default != "auto") else "local-model"

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body: dict[str, Any] = {"model": effective_model, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format

        logger.debug("LLM call provider=%s url=%s model=%s tools=%s", provider, url, effective_model, bool(tools))

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=timeout)
        except requests.exceptions.ConnectionError as exc:
            raise LLMUnavailableError(f"llm_connection_failed: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise LLMUnavailableError(f"llm_timeout: {exc}") from exc

        if resp.status_code >= 500:
            raise LLMUnavailableError(f"llm_server_error: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise LLMUnavailableError(
                f"llm_client_error: HTTP {resp.status_code} {resp.text[:200]}"
            )

        try:
            return resp.json()
        except Exception as exc:
            raise LLMUnavailableError(f"llm_invalid_json_response: {exc}") from exc

    @classmethod
    def invoke_with_tools(
        cls, prompt: str, tools: list, model: str | None = None, **kwargs
    ) -> dict:
        """Call LLM with tools= parameter. Returns dict with tool_calls list and content."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages

        response = cls._make_chat_call(messages, tools=tools, model=model)
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

        return {
            "tool_calls": tool_calls,
            "content": msg.get("content") or "",
            "finish_reason": choice.get("finish_reason"),
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
    def invoke(cls, prompt: str, model: str | None = None, **kwargs) -> str:
        """Plain chat completion. Returns content string."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(messages, model=model)
        choice = (response.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or ""
