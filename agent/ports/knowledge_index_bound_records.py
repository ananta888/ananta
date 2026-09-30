"""Read port for exact, hash-verified knowledge-index record hydration."""

from __future__ import annotations

from typing import Any, Protocol


class BoundKnowledgeRecordReader(Protocol):
    def load_bound_records(
        self,
        *,
        knowledge_index: Any,
        bindings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return the bound records in binding order; raise ``ValueError`` on mismatch."""
        ...


__all__ = ["BoundKnowledgeRecordReader"]
