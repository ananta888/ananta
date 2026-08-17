"""Hierarchical architecture context consumes the same retrieval service.

Prefill and expand/zoom stay tenant- and revision-bound by passing the
server capability through unchanged. This module does not open a second
vector or graph pipeline.
"""

from __future__ import annotations

from typing import Any, Mapping

from agent.services.codecompass_agentic_retrieval_contract import (
    MODE_GRAPH,
    MODE_HYBRID,
    SCHEMA_ID,
)
from agent.services.codecompass_agentic_retrieval_service import (
    get_codecompass_agentic_retrieval_service,
)


def retrieve_architecture_context(
    *,
    query: str,
    level: str = "system",
    expand: bool = False,
    capability: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Retrieve a hierarchical architecture slice through the shared contract."""

    mode = MODE_HYBRID if expand else MODE_GRAPH
    payload = {
        "schema": SCHEMA_ID,
        "kind": "request",
        "query": query,
        "mode": mode,
        "task_kind": "architecture_question",
        "budget": dict(budget or {"top_k": 6, "max_chars": 4000, "graph_depth": 1 if expand else 0}),
    }
    result = get_codecompass_agentic_retrieval_service().retrieve(payload, capability=capability)
    result.setdefault("diagnostics", {})
    result["diagnostics"]["architecture_level"] = str(level or "system")
    result["diagnostics"]["architecture_expand"] = bool(expand)
    return result
