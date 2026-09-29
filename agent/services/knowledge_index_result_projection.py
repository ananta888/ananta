"""Result projection for knowledge-index retrieval.

Turns ranked chunks into stable record dicts and extracts query-matching
passages from raw record content.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from agent.hybrid_orchestrator import ContextChunk


class KnowledgeIndexResultProjecting(Protocol):
    """What the retrieval service needs to shape ranked results."""

    def passage(self, raw_record: dict[str, Any], query: str, max_chars: int) -> dict[str, Any] | None: ...

    def record_projection(
        self, chunk: ContextChunk, authoritative_scope: Mapping[str, Any] | None
    ) -> dict[str, Any] | None: ...


class KnowledgeIndexResultProjector:
    """Projects ranked chunks/raw records into caller-facing shapes."""

    @staticmethod
    def passage(raw_record: dict[str, Any], query: str, max_chars: int) -> dict[str, Any] | None:
        from agent.services.knowledge_passage import best_passage

        content = raw_record.get("content") if isinstance(raw_record.get("content"), str) else raw_record.get("text")
        content = content if isinstance(content, str) else ""
        base_line = raw_record.get("start_line", raw_record.get("line_start"))
        if type(base_line) is not int or base_line <= 0:
            base_line = 1
            content, _header_lines = _strip_index_header(content, raw_record.get("path") or raw_record.get("file"))
        passage = best_passage(content, query, max_chars=max_chars, base_line=base_line)
        if passage is None:
            return None
        return {"text": passage.text, "line_start": passage.line_start, "line_end": passage.line_end}

    @staticmethod
    def record_projection(
        chunk: ContextChunk, authoritative_scope: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        metadata = dict(chunk.metadata or {})
        expected_scope = dict(authoritative_scope or {})
        observed_scope = {
            "tenant_id": metadata.get("tenant_id"),
            "workspace_id": metadata.get("workspace_id"),
            "repository_id": metadata.get("repository_id"),
            "revision": metadata.get("revision") or metadata.get("source_revision"),
            "source_scope": metadata.get("source_scope"),
        }
        if any(
            str(observed_scope.get(key) or "")
            and str(observed_scope.get(key)) != str(expected)
            for key, expected in expected_scope.items()
            if key in observed_scope and str(expected or "")
        ):
            return None
        return {
            "id": str(metadata.get("record_id") or metadata.get("chunk_id") or ""),
            # Keep the immutable record locator distinct from the
            # display source. An id-only record has no path and must
            # remain hydratable with an empty path selector.
            "path": str(metadata.get("repo_relative_path") or ""),
            "source": str(chunk.source or ""),
            "content": str(chunk.content or ""),
            "score": float(chunk.score or 0.0),
            "symbol": str(metadata.get("symbol") or ""),
            "kind": str(metadata.get("record_kind") or "retrieval_chunk"),
            "metadata": metadata,
            "verification_status": (
                "verified" if bool(metadata.get("source_id_verified")) else "unverified"
            ),
        }


def _strip_index_header(content: str, path: Any) -> tuple[str, int]:
    """Drop the ``# <path>`` / ``# Themen: …`` lines ``setup_codecompass_index`` prepends.

    Whole-file records carry that header in front of the file text, so line
    numbers counted in the record would be shifted by it.
    """
    lines = content.split("\n")
    if not path or not lines or lines[0].strip() != f"# {path}":
        return content, 0
    header = 2 if len(lines) > 1 and lines[1].startswith("# Themen: ") else 1
    return "\n".join(lines[header:]), header
