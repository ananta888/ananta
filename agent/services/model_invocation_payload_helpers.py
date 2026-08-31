"""Pure request and response projections for model invocation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def normalize_openai_tools(tools: list | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in list(tools or []):
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "function" and isinstance(item.get("function"), dict):
            normalized.append(item)
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(item.get("name") or function.get("name") or "").strip()
        if not name:
            continue
        description = str(item.get("description") or function.get("description") or "").strip()
        parameters = item.get("parameters")
        if not isinstance(parameters, dict):
            parameters = function.get("parameters")
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


def tool_calling_mode(profile: Any | None) -> str:
    if profile is None:
        return "native_tools"
    mode = str(getattr(profile, "tool_calling_mode", "") or "").strip()
    if mode:
        return mode
    return "native_tools" if bool(getattr(profile, "supports_tools", False)) else "none"


def max_output_tokens_for_request(profile: Any, provider_context: Any) -> int:
    configured = max(1, int(profile.max_output_tokens))
    if isinstance(provider_context, Mapping):
        raw_provider_limit = provider_context.get("max_completion_tokens_per_call")
    else:
        raw_provider_limit = getattr(provider_context, "max_completion_tokens_per_call", None)
    try:
        provider_limit = int(raw_provider_limit or 0)
    except (TypeError, ValueError):
        provider_limit = 0
    return min(configured, provider_limit) if provider_limit > 0 else configured


def messages_for_tool_mode(
    messages: list[dict],
    *,
    tools: list | None,
    tool_calling_mode: str,
) -> tuple[list[dict], bool]:
    normalized_tools = normalize_openai_tools(tools)
    if not normalized_tools:
        return messages, False
    if tool_calling_mode in {"native_tools", "both"}:
        return messages, True
    if tool_calling_mode != "prompt_json":
        return messages, False
    tool_contract = {
        "response_schema": {
            "type": "object",
            "required": ["tool", "args"],
            "properties": {
                "tool": {"type": "string"},
                "args": {"type": "object"},
                "confidence": {"type": "number"},
                "reasoning_summary": {"type": "string"},
            },
        },
        "allowed_tools": [
            {
                "name": item["function"]["name"],
                "description": item["function"].get("description") or "",
                "parameters": item["function"].get("parameters") or {"type": "object", "properties": {}},
            }
            for item in normalized_tools
        ],
    }
    system_msg = {
        "role": "system",
        "content": (
            "Return exactly one JSON object selecting a tool. Do not call tools directly. "
            f"Tool contract: {json.dumps(tool_contract, sort_keys=True)}"
        ),
    }
    return [system_msg] + [message for message in messages if isinstance(message, dict)], False


def blocked_candidates_as_dict(blocked: list[tuple[str, str]] | None) -> list[dict[str, Any]]:
    return [{"profile_id": profile_id, "reason": reason} for profile_id, reason in list(blocked or [])]


def fallback_error_type(error: Any) -> str:
    profile = list(getattr(error, "llm_call_profile", []) or [])
    if profile and isinstance(profile[-1], dict):
        return str(profile[-1].get("error_type") or "unknown")
    return str(getattr(error, "terminal_reason", "") or "unknown")


def finalize_trace_error(prompt_trace: Any, trace_service: Any, error_type: str, error_message: str) -> None:
    if prompt_trace is None or trace_service is None:
        return
    try:
        finalized = trace_service.finalize_trace(
            prompt_trace,
            success=False,
            error_type=error_type,
            error_message=error_message,
        )
        trace_service.store(finalized)
    except Exception:
        pass


def response_message(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    choice = (payload.get("choices") or [{}])[0] if isinstance(payload, dict) else {}
    choice = choice if isinstance(choice, dict) else {}
    message = choice.get("message")
    return choice, message if isinstance(message, dict) else {}
