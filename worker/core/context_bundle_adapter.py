"""ContextEnvelopeAdapter: bridges Hub context_envelope_ref to ContextResolver. AWF-T018.

The Hub sends a context_envelope_ref — either a bare string ID or a structured
dict with context_bundle_id, context_hash, and retrieval_refs.
This adapter converts it into resolved ContextBlocks for the worker.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from worker.core.context_resolver import (
    ContextBlock,
    ContextResolver,
    ContextSensitivity,
    _estimate_tokens,
)


# ── ContextEnvelopeRef ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContextEnvelopeRef:
    """Normalized Hub context_envelope_ref. AWF-T018."""
    bundle_id: str
    context_hash: str
    retrieval_refs: list[dict[str, Any]] = field(default_factory=list)
    context_byte_limit: int = 120_000
    context_chunk_limit: int = 32
    access_policy_id: Optional[str] = None # T018
    access_policy_version: Optional[int] = None # T018
    destination_context_hash: Optional[str] = None # T018
    denied_block_count: int = 0 # T018
    redacted_block_count: int = 0 # T018
    summarized_block_count: int = 0 # T018
    context_access_summary: Optional[str] = None # T018

    @classmethod
    def from_raw(cls, raw: str | dict[str, Any]) -> "ContextEnvelopeRef":
        """Parse Hub's context_envelope_ref (string or dict). AWF-T018."""
        if isinstance(raw, str):
            return cls(bundle_id=raw.strip(), context_hash="")
        d = dict(raw or {})
        return cls(
            bundle_id=str(d.get("context_bundle_id") or d.get("bundle_id") or "").strip(),
            context_hash=str(d.get("context_hash") or "").strip(),
            retrieval_refs=list(d.get("retrieval_refs") or []),
            context_byte_limit=int(d.get("context_byte_limit") or 120_000),
            context_chunk_limit=int(d.get("context_chunk_limit") or 32),
            access_policy_id=d.get("access_policy_id"),
            access_policy_version=d.get("access_policy_version"),
            destination_context_hash=d.get("destination_context_hash"),
            denied_block_count=int(d.get("denied_block_count") or 0),
            redacted_block_count=int(d.get("redacted_block_count") or 0),
            summarized_block_count=int(d.get("summarized_block_count") or 0),
            context_access_summary=d.get("context_access_summary"),
        )

    def is_empty(self) -> bool:
        return not self.bundle_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_bundle_id": self.bundle_id,
            "context_hash": self.context_hash,
            "retrieval_refs": self.retrieval_refs,
            "context_byte_limit": self.context_byte_limit,
            "context_chunk_limit": self.context_chunk_limit,
            "access_policy_id": self.access_policy_id,
            "access_policy_version": self.access_policy_version,
            "destination_context_hash": self.destination_context_hash,
            "denied_block_count": self.denied_block_count,
            "redacted_block_count": self.redacted_block_count,
            "summarized_block_count": self.summarized_block_count,
            "context_access_summary": self.context_access_summary,
        }


# ── ContextEnvelopeAdapter ────────────────────────────────────────────────────

class ContextEnvelopeAdapter:
    """Converts Hub context_envelope_ref → resolved ContextBlocks. AWF-T018.

    Wraps ContextResolver so all callers use a uniform interface regardless
    of whether context comes from a structured ContextBundleDB, a pre-loaded
    dict, or raw retrieval refs from the Hub envelope.
    """

    def __init__(self, resolver: ContextResolver | None = None) -> None:
        self._resolver = resolver or ContextResolver()

    def resolve(
        self,
        context_envelope_ref: str | dict[str, Any],
        *,
        preloaded_blocks: list[ContextBlock] | None = None,
        allowed_source_types: list[str] | None = None,
    ) -> tuple[list[ContextBlock], list[str]]:
        """Resolve a Hub context_envelope_ref into ContextBlocks. AWF-T018.

        If preloaded_blocks is given, use them directly (for test injection
        and cached context). Otherwise, resolve from retrieval_refs.

        Returns (blocks, errors).
        """
        ref = ContextEnvelopeRef.from_raw(context_envelope_ref)

        if ref.is_empty():
            return [], ["context_envelope_empty: no bundle_id provided"]

        if preloaded_blocks is not None:
            return list(preloaded_blocks), []

        if ref.retrieval_refs:
            return self._resolver.resolve(
                ref.retrieval_refs,
                allowed_source_types=allowed_source_types,
            )

        # No retrieval_refs and no preloaded — return a minimal task-description block
        stub = ContextBlock(
            source_type="task_description",
            origin_id=ref.bundle_id,
            provenance=f"context_envelope:{ref.bundle_id}",
            sensitivity=ContextSensitivity.project_internal,
            token_estimate=0,
            content="",
            priority=10,
        )
        return [stub], []

    def resolve_from_bundle(
        self,
        bundle_id: str,
        *,
        content: str,
        source_type: str = "task_description",
        sensitivity: ContextSensitivity = ContextSensitivity.project_internal,
        priority: int = 10,
    ) -> ContextBlock:
        """Build a single ContextBlock from a ContextBundleDB payload. AWF-T018."""
        token_est = _estimate_tokens(content)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16] if content else ""
        return ContextBlock(
            source_type=source_type,
            origin_id=bundle_id,
            provenance=f"context_bundle_db:{bundle_id}",
            sensitivity=sensitivity,
            token_estimate=token_est,
            content=content,
            content_hash=content_hash,
            priority=priority,
        )
