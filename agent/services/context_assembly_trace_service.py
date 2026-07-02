"""ContextAssemblyTraceService — T04

Verfolgt welche Context-Teile in einen LLM-Prompt eingebaut wurden,
ohne jemals den vollständigen Prompt-Text zu speichern.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextPart:
    source_type: str     # "system"|"user"|"history"|"tool_schemas"|"rag_context"|"file_context"|"worker_instruction"|"output_budget"
    source_ref: str      # kurze Referenz — kein Volltext
    estimated_tokens: int
    included: bool
    blocked_reason: str | None
    content_hash: str | None   # sha256[:16] wenn store_prompt_hashes=True

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "estimated_tokens": self.estimated_tokens,
            "included": self.included,
            "blocked_reason": self.blocked_reason,
            "content_hash": self.content_hash,
        }


@dataclass
class ContextAssemblyTrace:
    trace_ref: str
    decision_ref: str
    parts: list[ContextPart] = field(default_factory=list)
    estimated_input_tokens: int = 0   # sum of included parts
    reserved_output_tokens: int = 0
    truncated_parts: list[str] = field(default_factory=list)  # source_types die abgeschnitten wurden

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_ref": self.trace_ref,
            "decision_ref": self.decision_ref,
            "parts": [p.as_dict() for p in self.parts],
            "estimated_input_tokens": self.estimated_input_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "truncated_parts": list(self.truncated_parts),
        }


class ContextAssemblyTraceService:
    """Traces context assembly without ever storing raw prompt text."""

    def __init__(self, store_prompt_hashes: bool = True, store_prompt_text: bool = False) -> None:
        if store_prompt_text:
            raise ValueError(
                "store_prompt_text=True is not permitted — "
                "ContextAssemblyTraceService never stores raw prompt text."
            )
        self.store_prompt_hashes = store_prompt_hashes

    def start_trace(self, *, decision_ref: str) -> ContextAssemblyTrace:
        """Create a new empty trace linked to a decision."""
        return ContextAssemblyTrace(
            trace_ref=str(uuid.uuid4()),
            decision_ref=str(decision_ref or ""),
        )

    def add_part(
        self,
        trace: ContextAssemblyTrace,
        *,
        source_type: str,
        source_ref: str,
        text: str | None,
        estimated_tokens: int,
        included: bool,
        blocked_reason: str | None = None,
    ) -> None:
        """Add a context part to the trace.

        `text` is only used to compute a hash and is never stored in the trace.
        """
        content_hash: str | None = None
        if self.store_prompt_hashes and text is not None:
            content_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]

        part = ContextPart(
            source_type=str(source_type or ""),
            source_ref=str(source_ref or ""),
            estimated_tokens=max(0, int(estimated_tokens)),
            included=bool(included),
            blocked_reason=str(blocked_reason) if blocked_reason else None,
            content_hash=content_hash,
        )
        trace.parts.append(part)

    def finalize(
        self,
        trace: ContextAssemblyTrace,
        *,
        reserved_output_tokens: int = 0,
    ) -> ContextAssemblyTrace:
        """Compute derived fields and mark the trace as finalized."""
        trace.estimated_input_tokens = sum(
            p.estimated_tokens for p in trace.parts if p.included
        )
        trace.reserved_output_tokens = max(0, int(reserved_output_tokens))
        # Collect source_types that were added but later excluded (truncated)
        included_types = {p.source_type for p in trace.parts if p.included}
        excluded_types = {p.source_type for p in trace.parts if not p.included}
        trace.truncated_parts = sorted(excluded_types - included_types)
        return trace
