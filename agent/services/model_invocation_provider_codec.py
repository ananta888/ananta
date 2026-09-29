"""Provider wire codec of ModelInvocationService: build provider request
bodies (OpenAI-compatible, Ollama generate) and normalize responses."""

from __future__ import annotations

import json
from typing import Any

from agent.services.local_runtime_response_adapters import (
    LocalRuntimeResponseError,
    normalize_ollama_chat,
    normalize_ollama_generate,
)
from agent.services.model_invocation_errors import LLMUnavailableError
from agent.services.model_invocation_support import (
    composed_model_invocation_service,
)
from ananta_contracts.provider_endpoint_policy import (
    normalize_provider_endpoint_identity,
)


class ModelInvocationProviderCodecMixin:
    """Translate between the internal chat shape and provider wire formats."""

    @staticmethod
    def _provider_response_redirect_denied(
        *,
        provider: str,
        request_url: str,
        response: Any,
    ) -> bool:
        if 300 <= int(response.status_code) < 400:
            return True
        response_url = str(getattr(response, "url", "") or "").strip()
        if not response_url:
            return False
        try:
            return normalize_provider_endpoint_identity(
                provider_id=provider,
                endpoint_url=response_url,
            ) != normalize_provider_endpoint_identity(
                provider_id=provider,
                endpoint_url=request_url,
            )
        except ValueError:
            return True

    @staticmethod
    def _ollama_generate_request_body(
        *,
        messages: list[dict],
        model: str,
        profile: Any,
        provider_context: Any,
        tools_requested: bool,
    ) -> dict[str, Any]:
        if tools_requested:
            raise LLMUnavailableError(
                "ollama_generate_native_tools_unsupported",
                terminal_reason="policy_blocked",
            )
        system_prompt = "\n".join(
            str(message.get("content") or "")
            for message in messages
            if (isinstance(message, dict) and str(message.get("role") or "").strip().lower() == "system")
        ).strip()
        prompt = "\n".join(
            f"{str(message.get('role') or 'user')}: {str(message.get('content') or '')}"
            for message in messages
            if (isinstance(message, dict) and str(message.get("role") or "").strip().lower() != "system")
        )
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            body["system"] = system_prompt
        if profile is not None:
            body["options"] = {
                "temperature": float(profile.temperature),
                "num_predict": (
                    composed_model_invocation_service()._max_output_tokens_for_request(
                        profile,
                        provider_context,
                    )
                ),
            }
        return body

    @staticmethod
    def _normalize_ollama_generate_response(
        payload: Any,
        *,
        model: str,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        try:
            normalized = normalize_ollama_generate(payload)
        except LocalRuntimeResponseError:
            return {}
        prompt_tokens = int(normalized["usage"]["prompt_tokens"] or 0)
        completion_tokens = int(normalized["usage"]["completion_tokens"] or 0)
        return {
            "choices": [
                {
                    "message": {
                        "content": normalized["content"],
                        "tool_calls": [],
                        "reasoning_content": normalized["thinking"],
                    },
                    "finish_reason": normalized["finish_reason"] or (
                        "stop" if normalized["done"] else None
                    ),
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "model": str(payload.get("model") or model),
        }

    @staticmethod
    def _normalize_ollama_chat_response(
        payload: Any,
        *,
        model: str,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        try:
            normalized = normalize_ollama_chat(payload)
        except LocalRuntimeResponseError:
            return {}
        prompt_tokens = int(normalized["usage"]["prompt_tokens"] or 0)
        completion_tokens = int(normalized["usage"]["completion_tokens"] or 0)
        tool_calls = [
            {
                "id": item["id"],
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": json.dumps(
                        item["arguments"],
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            }
            for item in normalized["tool_calls"]
        ]
        return {
            "choices": [
                {
                    "message": {
                        "content": normalized["content"],
                        "tool_calls": tool_calls,
                        "reasoning_content": normalized["thinking"],
                    },
                    "finish_reason": normalized["finish_reason"] or (
                        "stop" if normalized["done"] else None
                    ),
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "model": str(payload.get("model") or model),
        }

    @classmethod
    def _provider_request_body(
        cls,
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
    ) -> tuple[dict[str, Any], bool]:
        from agent.services.model_prompt_prefix_service import (
            ModelPromptPrefixService,
        )

        effective_messages = ModelPromptPrefixService.apply(
            messages,
            profile=profile,
        )
        ollama_generate = provider == "ollama" and str(url).endswith("/api/generate")
        if ollama_generate:
            return (
                cls._ollama_generate_request_body(
                    messages=effective_messages,
                    model=model,
                    profile=profile,
                    provider_context=provider_context,
                    tools_requested=bool(tools and send_native_tools),
                ),
                True,
            )
        body: dict[str, Any] = {
            "model": model,
            "messages": effective_messages,
        }
        if profile is not None:
            body["temperature"] = float(profile.temperature)
            body["max_tokens"] = cls._max_output_tokens_for_request(
                profile,
                provider_context,
            )
        if tools and send_native_tools:
            body["tools"] = cls._normalize_openai_tools(tools)
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format
        return body, False

    @classmethod
    def _normalize_provider_response(
        cls,
        payload: Any,
        *,
        ollama_generate: bool,
        ollama_chat: bool,
        model: str,
    ) -> Any:
        if ollama_generate:
            return cls._normalize_ollama_generate_response(
                payload,
                model=model,
            )
        if ollama_chat:
            return cls._normalize_ollama_chat_response(
                payload,
                model=model,
            )
        return payload
