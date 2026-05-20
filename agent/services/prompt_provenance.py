"""PromptProvenanceChain: ordered provenance entries for LLM prompt construction. PTI-008."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


ENTRY_TYPES = {
    "system_default",
    "goal_mode",
    "planning_prompt_version",
    "blueprint",
    "blueprint_role",
    "template_catalog_entry",
    "role_template",
    "model_profile",
    "overlay_prompt",
    "context_compactor",
    "rag_context",
    "tool_definitions",
    "prompt_optimizer",
    "final_render",
    "inline_fallback",
}


@dataclass
class ProvenanceEntry:
    type: str
    order: int
    id: str | None = None
    name: str | None = None
    version: str | None = None
    source_path: str | None = None
    checksum: str | None = None
    applied: bool = True
    reason_not_applied: str | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "order": self.order,
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "source_path": self.source_path,
            "checksum": self.checksum,
            "applied": self.applied,
            "reason_not_applied": self.reason_not_applied,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProvenanceEntry":
        return cls(
            type=d.get("type") or "unknown",
            order=int(d.get("order") or 0),
            id=d.get("id"),
            name=d.get("name"),
            version=d.get("version"),
            source_path=d.get("source_path"),
            checksum=d.get("checksum"),
            applied=bool(d.get("applied", True)),
            reason_not_applied=d.get("reason_not_applied"),
            input_hash=d.get("input_hash"),
            output_hash=d.get("output_hash"),
            summary=d.get("summary"),
        )


class PromptProvenanceChain:
    """Builds and holds an ordered chain of provenance entries."""

    def __init__(self) -> None:
        self._entries: list[ProvenanceEntry] = []

    def add(
        self,
        *,
        type: str,
        id: str | None = None,
        name: str | None = None,
        version: str | None = None,
        source_path: str | None = None,
        checksum: str | None = None,
        applied: bool = True,
        reason_not_applied: str | None = None,
        input_hash: str | None = None,
        output_hash: str | None = None,
        summary: str | None = None,
    ) -> "PromptProvenanceChain":
        order = len(self._entries)
        entry = ProvenanceEntry(
            type=type,
            order=order,
            id=id,
            name=name,
            version=version,
            source_path=source_path,
            checksum=checksum,
            applied=applied,
            reason_not_applied=reason_not_applied,
            input_hash=input_hash,
            output_hash=output_hash,
            summary=summary,
        )
        self._entries.append(entry)
        return self

    def add_planning_prompt(
        self,
        *,
        prompt_version_id: str,
        version: str,
        language: str,
        mode: str,
        checksum: str,
        source_path: str = "config/planning_prompts.default.json",
        is_inline_fallback: bool = False,
    ) -> "PromptProvenanceChain":
        entry_type = "inline_fallback" if is_inline_fallback else "planning_prompt_version"
        return self.add(
            type=entry_type,
            id=prompt_version_id,
            name=f"planning:{mode}:{language}",
            version=version,
            source_path=None if is_inline_fallback else source_path,
            checksum=checksum,
            applied=True,
            summary=f"mode={mode} lang={language}",
        )

    def add_optimizer_step(
        self,
        *,
        name: str,
        reason: str | None = None,
        input_hash: str | None = None,
        output_hash: str | None = None,
        changed: bool = False,
    ) -> "PromptProvenanceChain":
        return self.add(
            type="prompt_optimizer",
            name=name,
            applied=changed,
            reason_not_applied=None if changed else "no_change",
            input_hash=input_hash,
            output_hash=output_hash,
            summary=reason,
        )

    def add_final_render(self, *, output_hash: str | None = None) -> "PromptProvenanceChain":
        return self.add(type="final_render", output_hash=output_hash)

    def entries(self) -> list[ProvenanceEntry]:
        return list(self._entries)

    def to_list(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._entries]

    @classmethod
    def from_list(cls, data: list[dict]) -> "PromptProvenanceChain":
        chain = cls()
        for d in sorted(data or [], key=lambda x: x.get("order") or 0):
            chain._entries.append(ProvenanceEntry.from_dict(d))
        return chain


def text_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
