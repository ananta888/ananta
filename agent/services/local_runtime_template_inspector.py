"""Deterministic, content-free chat-template classification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemplateInspection:
    family: str
    sha256: str | None
    conflict: bool


class LocalRuntimeTemplateInspector:
    _TOKENS = {
        "chatml": ("<|im_start|>", "<|im_end|>"),
        "llama3": ("<|start_header_id|>", "<|eot_id|>"),
        "mistral": ("[INST]", "[/INST]"),
        "gemma": ("<start_of_turn>", "<end_of_turn>"),
        "hermes": ("<|im_start|>system", "<tool_call>"),
        "phi": ("<|system|>", "<|assistant|>"),
    }

    def inspect(self, template: object) -> TemplateInspection:
        if not isinstance(template, str) or not template:
            return TemplateInspection("unknown", None, False)
        digest = hashlib.sha256(template.encode("utf-8", errors="replace")).hexdigest()
        if len(template.encode("utf-8", errors="replace")) > 256 * 1024:
            return TemplateInspection("unknown", digest, False)
        matches = [family for family, tokens in self._TOKENS.items() if all(token in template for token in tokens)]
        # Hermes is a constrained ChatML dialect and wins when it is the only
        # additional match. Any other mixture is an incompatible token grammar.
        if set(matches) == {"chatml", "hermes"}:
            return TemplateInspection("hermes", digest, False)
        if len(matches) > 1:
            return TemplateInspection("conflict", digest, True)
        return TemplateInspection(matches[0] if matches else "unknown", digest, False)


__all__ = ["LocalRuntimeTemplateInspector", "TemplateInspection"]
