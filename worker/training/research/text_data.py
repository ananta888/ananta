"""Deterministic text and supervised-token preparation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from worker.training.tokenizers.byte_bpe import ByteBpeTokenizer


def record_text(record: Mapping[str, object]) -> str:
    if isinstance(record.get("text"), str):
        return str(record["text"])
    if isinstance(record.get("messages"), Sequence) and not isinstance(record.get("messages"), (str, bytes)):
        rendered: list[str] = []
        for message in record["messages"]:  # type: ignore[index]
            if not isinstance(message, Mapping) or set(message) != {"role", "content"}:
                raise ValueError("research_chat_message_invalid")
            role = str(message.get("role") or "").strip().lower()
            content = str(message.get("content") or "")
            if role not in {"system", "user", "assistant", "tool"}:
                raise ValueError("research_chat_role_invalid")
            rendered.append(f"<{role}>\n{content}\n</{role}>\n")
        return "".join(rendered)
    prompt = record.get("prompt")
    response = record.get("response")
    if isinstance(prompt, str) and isinstance(response, str):
        return f"<user>\n{prompt}\n</user>\n<assistant>\n{response}\n</assistant>\n"
    raise ValueError("research_record_text_missing")


@dataclass(frozen=True, slots=True)
class SupervisedTokens:
    token_ids: tuple[int, ...]
    loss_mask: tuple[bool, ...]


def supervised_tokens(record: Mapping[str, object], tokenizer: ByteBpeTokenizer) -> SupervisedTokens:
    messages = record.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        ids = tuple(tokenizer.encode(record_text(record)))
        return SupervisedTokens(ids, tuple(True for _ in ids))
    token_ids: list[int] = []
    loss_mask: list[bool] = []
    for message in messages:
        if not isinstance(message, Mapping) or set(message) != {"role", "content"}:
            raise ValueError("research_chat_message_invalid")
        role = str(message.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("research_chat_role_invalid")
        prefix = tokenizer.encode(f"<{role}>\n")
        content = tokenizer.encode(str(message.get("content") or ""))
        suffix = tokenizer.encode(f"\n</{role}>\n")
        token_ids.extend(prefix + content + suffix)
        # Only assistant-authored content contributes to the SFT loss.  Role
        # framing and forced system/user/tool tokens are always masked.
        loss_mask.extend([False] * len(prefix))
        loss_mask.extend([role == "assistant"] * len(content))
        loss_mask.extend([False] * len(suffix))
    return SupervisedTokens(tuple(token_ids), tuple(loss_mask))


__all__ = ["SupervisedTokens", "record_text", "supervised_tokens"]
