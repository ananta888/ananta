from __future__ import annotations

from typing import Any


class ResponseValidator:
    """Validates OpenAI-compatible response shape and stream chunks."""

    def validate_chat_completion(self, payload: dict[str, Any]) -> tuple[bool, str]:
        if not isinstance(payload, dict):
            return False, "response_not_object"
        required = {"id", "object", "model", "choices"}
        missing = [k for k in required if k not in payload]
        if missing:
            return False, f"missing_fields:{','.join(missing)}"
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return False, "choices_invalid"
        first = choices[0] if isinstance(choices[0], dict) else {}
        if "message" not in first or not isinstance(first.get("message"), dict):
            return False, "choice_message_invalid"
        tool_calls = first["message"].get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list):
                return False, "tool_calls_invalid"
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    return False, "tool_call_item_invalid"
        return True, "ok"

    def validate_stream_chunk(self, line: str) -> tuple[bool, str]:
        text = str(line or "").strip()
        if not text:
            return True, "empty"
        if not text.startswith("data: "):
            return False, "missing_data_prefix"
        if text == "data: [DONE]":
            return True, "done"
        return True, "ok"

