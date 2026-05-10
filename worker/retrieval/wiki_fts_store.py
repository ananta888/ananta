from __future__ import annotations

from worker.retrieval.codecompass_fts_store import CodeCompassFtsStore


class WikiFtsStore(CodeCompassFtsStore):
    """Wiki-specific alias around the existing FTS implementation."""

