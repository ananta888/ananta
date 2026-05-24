from __future__ import annotations

import json
from typing import Any


class RepairController:
    """Bounded repair for malformed structured outputs (format only)."""

    def __init__(self, *, max_attempts: int = 1, enabled: bool = True) -> None:
        self.max_attempts = max(0, int(max_attempts))
        self.enabled = bool(enabled)

    def repair_chat_completion(self, malformed: Any, *, model: str) -> tuple[dict[str, Any] | None, str]:
        _ = model
        if not self.enabled or self.max_attempts < 1:
            return None, "repair_disabled"
        # One bounded attempt: if malformed is json string, parse and verify minimal envelope.
        raw = malformed
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return None, "repair_failed_invalid_json"
        if not isinstance(raw, dict):
            return None, "repair_failed_not_object"
        # Only envelope repair. Never invent tool results or file contents.
        if "choices" not in raw and "text" in raw:
            raw["choices"] = [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": str(raw.get("text") or "")},
                }
            ]
        for key, default in (("id", "chatcmpl-repaired"), ("object", "chat.completion"), ("model", "unknown")):
            raw.setdefault(key, default)
        return raw, "repaired"

