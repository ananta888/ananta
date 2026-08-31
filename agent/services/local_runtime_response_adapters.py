"""Bounded provider response normalization for Ollama and LM Studio."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


class LocalRuntimeResponseError(ValueError):
    pass


class OllamaChatStreamAccumulator:
    """Combine bounded native chat chunks without mixing thinking and output."""

    def __init__(self, *, maximum_text_chars: int = 1_000_000) -> None:
        self._maximum = maximum_text_chars
        self._content: list[str] = []
        self._thinking: list[str] = []
        self._tool_calls: dict[str, dict[str, Any]] = {}
        self._done = False
        self._finish_reason: str | None = None
        self._usage: dict[str, int | None] = {"prompt_tokens": None, "completion_tokens": None}

    def push(self, payload: Mapping[str, Any]) -> None:
        if self._done:
            raise LocalRuntimeResponseError("ollama_stream_already_done")
        normalized = normalize_ollama_chat(payload, maximum_text_chars=self._maximum)
        self._content.append(normalized["content"])
        if normalized["thinking"]:
            self._thinking.append(normalized["thinking"])
        for call in normalized["tool_calls"]:
            existing = self._tool_calls.get(call["id"])
            if existing is not None and existing != call:
                raise LocalRuntimeResponseError("ollama_stream_tool_call_conflict")
            self._tool_calls[call["id"]] = call
        if sum(map(len, self._content)) > self._maximum or sum(map(len, self._thinking)) > self._maximum:
            raise LocalRuntimeResponseError("local_runtime_response_too_large")
        self._done = normalized["done"]
        self._finish_reason = normalized["finish_reason"] or self._finish_reason
        if any(value is not None for value in normalized["usage"].values()):
            self._usage = normalized["usage"]

    def result(self) -> dict[str, Any]:
        return {
            "schema": "ananta.local-runtime-response.v1",
            "content": "".join(self._content),
            "thinking": "".join(self._thinking) or None,
            "tool_calls": list(self._tool_calls.values()),
            "done": self._done,
            "finish_reason": self._finish_reason,
            "usage": dict(self._usage),
        }


def normalize_ollama_chat(payload: Mapping[str, Any], *, maximum_text_chars: int = 1_000_000) -> dict[str, Any]:
    message = payload.get("message")
    if not isinstance(message, Mapping):
        raise LocalRuntimeResponseError("ollama_chat_response_invalid")
    content = _bounded_text(message.get("content"), maximum_text_chars)
    thinking = _bounded_optional_text(message.get("thinking"), maximum_text_chars)
    calls = _tool_calls(message.get("tool_calls"))
    return {
        "schema": "ananta.local-runtime-response.v1",
        "content": content,
        "thinking": thinking,
        "tool_calls": calls,
        "done": bool(payload.get("done", False)),
        "finish_reason": str(payload.get("done_reason") or "") or None,
        "usage": _ollama_usage(payload),
    }


def normalize_ollama_generate(payload: Mapping[str, Any], *, maximum_text_chars: int = 1_000_000) -> dict[str, Any]:
    return {
        "schema": "ananta.local-runtime-response.v1",
        "content": _bounded_text(payload.get("response"), maximum_text_chars),
        "thinking": _bounded_optional_text(payload.get("thinking"), maximum_text_chars),
        "tool_calls": [],
        "done": bool(payload.get("done", False)),
        "finish_reason": str(payload.get("done_reason") or "") or None,
        "usage": _ollama_usage(payload),
    }


def normalize_openai_chat(payload: Mapping[str, Any], *, maximum_text_chars: int = 1_000_000) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise LocalRuntimeResponseError("openai_chat_response_invalid")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise LocalRuntimeResponseError("openai_chat_response_invalid")
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    return {
        "schema": "ananta.local-runtime-response.v1",
        "content": _bounded_text(message.get("content"), maximum_text_chars),
        "thinking": _bounded_optional_text(message.get("reasoning_content"), maximum_text_chars),
        "tool_calls": _tool_calls(message.get("tool_calls")),
        "done": True,
        "finish_reason": str(choice.get("finish_reason") or "") or None,
        "usage": {
            "prompt_tokens": _bounded_count(usage.get("prompt_tokens")),
            "completion_tokens": _bounded_count(usage.get("completion_tokens")),
        },
    }


def normalize_ollama_embedding(
    payload: Mapping[str, Any], *, expected_dimension: int | None = None, maximum_dimension: int = 65_536
) -> tuple[float, ...]:
    raw = payload.get("embeddings")
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        raw = raw[0]
    if not isinstance(raw, list) or not raw or len(raw) > maximum_dimension:
        raise LocalRuntimeResponseError("embedding_response_invalid")
    vector = tuple(float(item) for item in raw)
    if any(not math.isfinite(item) for item in vector):
        raise LocalRuntimeResponseError("embedding_response_invalid")
    if expected_dimension is not None and len(vector) != expected_dimension:
        raise LocalRuntimeResponseError("embedding_dimension_mismatch")
    return vector


def _tool_calls(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > 64:
        raise LocalRuntimeResponseError("tool_calls_invalid")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise LocalRuntimeResponseError("tool_calls_invalid")
        function = raw.get("function") if isinstance(raw.get("function"), Mapping) else raw
        name = str(function.get("name") or "").strip()
        if not name or len(name) > 192:
            raise LocalRuntimeResponseError("tool_calls_invalid")
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise LocalRuntimeResponseError("tool_arguments_invalid") from exc
        if not isinstance(arguments, Mapping):
            raise LocalRuntimeResponseError("tool_arguments_invalid")
        encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded) > 256 * 1024:
            raise LocalRuntimeResponseError("tool_arguments_too_large")
        result.append({
            "id": str(raw.get("id") or f"call-{index}"),
            "name": name,
            "arguments": dict(arguments),
        })
    return result


def _bounded_text(value: object, maximum: int) -> str:
    text = str(value or "")
    if len(text) > maximum:
        raise LocalRuntimeResponseError("local_runtime_response_too_large")
    return text


def _bounded_optional_text(value: object, maximum: int) -> str | None:
    text = _bounded_text(value, maximum)
    return text or None


def _bounded_count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 10_000_000_000 else None


def _ollama_usage(payload: Mapping[str, Any]) -> dict[str, int | None]:
    return {
        "prompt_tokens": _bounded_count(payload.get("prompt_eval_count")),
        "completion_tokens": _bounded_count(payload.get("eval_count")),
    }


__all__ = [
    "LocalRuntimeResponseError",
    "OllamaChatStreamAccumulator",
    "normalize_ollama_chat",
    "normalize_ollama_embedding",
    "normalize_ollama_generate",
    "normalize_openai_chat",
]
