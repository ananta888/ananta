"""Deterministic rendering of admitted chat records for local training."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def render_chat_messages(
    messages: Sequence[Mapping[str, Any]],
    tokenizer: Any,
    *,
    add_generation_prompt: bool,
) -> str:
    """Use a configured template or a stable role/content fallback."""
    apply_template = getattr(tokenizer, "apply_chat_template", None)
    chat_template = getattr(tokenizer, "chat_template", None)
    if callable(apply_template) and isinstance(chat_template, str) and chat_template.strip():
        return str(
            apply_template(
                list(messages),
                tokenize=False,
                add_generation_prompt=add_generation_prompt,
            )
        )
    rendered = "\n".join(
        f"{str(message.get('role') or 'user')}: {str(message.get('content') or '')}" for message in messages
    )
    if add_generation_prompt:
        return f"{rendered}\nassistant:"
    return rendered


__all__ = ["render_chat_messages"]
