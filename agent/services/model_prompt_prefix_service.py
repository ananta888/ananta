"""Apply declarative, profile-scoped system-prompt prefixes."""

from __future__ import annotations

from typing import Any


class ModelPromptPrefixService:
    """Build request-local messages without mutating the caller's prompt."""

    @staticmethod
    def apply(
        messages: list[dict[str, Any]],
        *,
        profile: Any,
    ) -> list[dict[str, Any]]:
        copied = [dict(message) for message in messages]
        prefix = str(
            getattr(profile, "system_prompt_prefix", None) or ""
        ).strip()
        if not prefix:
            return copied

        for message in copied:
            if str(message.get("role") or "").strip().lower() != "system":
                continue
            content = message.get("content")
            if not isinstance(content, str):
                break
            normalized_content = content.lstrip()
            if normalized_content.startswith(prefix):
                message["content"] = normalized_content
            elif normalized_content:
                message["content"] = f"{prefix}\n{normalized_content}"
            else:
                message["content"] = prefix
            return copied

        copied.insert(0, {"role": "system", "content": prefix})
        return copied
