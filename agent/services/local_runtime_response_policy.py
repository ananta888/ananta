"""Policy strategies for treating local-model responses as untrusted input."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


class LocalRuntimeResponsePolicyError(ValueError):
    pass


class LocalRuntimeResponsePolicy:
    """Apply an opt-in parser policy without authorizing any model action."""

    _QWEN_POLICY = "qwen3_reasoning_safe"
    _THINK_BLOCK = re.compile(r"\A\s*<think>(.*?)</think>\s*(.*)\Z", re.DOTALL)

    def apply(
        self,
        payload: Mapping[str, Any],
        *,
        policy_id: str | None,
        tools_requested: bool,
    ) -> dict[str, Any]:
        copied = self._copy(payload)
        if policy_id != self._QWEN_POLICY:
            return copied
        choices = copied.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LocalRuntimeResponsePolicyError("local_runtime_response_shape_invalid")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise LocalRuntimeResponsePolicyError("local_runtime_response_shape_invalid")
        content = str(message.get("content") or "")
        native_reasoning = str(message.get("reasoning_content") or "")
        parsed_reasoning = ""
        if "<think" in content.lower() or "</think>" in content.lower():
            if native_reasoning:
                raise LocalRuntimeResponsePolicyError("local_runtime_reasoning_ambiguous")
            matched = self._THINK_BLOCK.fullmatch(content)
            if matched is None or "<think" in matched.group(2).lower():
                raise LocalRuntimeResponsePolicyError("local_runtime_reasoning_markup_invalid")
            parsed_reasoning, content = matched.groups()
            message["content"] = content
        reasoning = native_reasoning or parsed_reasoning
        if tools_requested and "<tool_call" in content.lower():
            raise LocalRuntimeResponsePolicyError("local_runtime_unparsed_tool_markup")
        if reasoning and self._reasoning_leaks_into_tools(reasoning, message.get("tool_calls")):
            raise LocalRuntimeResponsePolicyError("local_runtime_reasoning_tool_overlap")
        message.pop("reasoning_content", None)
        metadata = copied.get("metadata") if isinstance(copied.get("metadata"), dict) else {}
        metadata["reasoning_observation"] = {
            "schema": "ananta.reasoning-observation.v1",
            "present": bool(reasoning),
            "content_sha256": hashlib.sha256(reasoning.encode("utf-8")).hexdigest() if reasoning else None,
            "character_count": len(reasoning),
            "persisted": False,
            "authorization_input": False,
        }
        copied["metadata"] = metadata
        return copied

    @staticmethod
    def _copy(payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(json.dumps(dict(payload), allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise LocalRuntimeResponsePolicyError("local_runtime_response_not_json") from exc

    @staticmethod
    def _reasoning_leaks_into_tools(reasoning: str, calls: object) -> bool:
        if not isinstance(calls, list) or len(reasoning) < 16:
            return False
        rendered = json.dumps(calls, sort_keys=True, ensure_ascii=False)
        return reasoning in rendered


def configured_response_policy(profile: object) -> str | None:
    extra = getattr(profile, "extra", None)
    if not isinstance(extra, Mapping):
        return None
    value = str(extra.get("response_policy") or "").strip()
    return value or None


__all__ = [
    "LocalRuntimeResponsePolicy",
    "LocalRuntimeResponsePolicyError",
    "configured_response_policy",
]
