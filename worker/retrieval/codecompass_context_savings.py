"""COMBO-007: token-savings metrics for context packages.

Per todo acceptance:

* context packages contain estimated_context_savings with
  baseline_tokens, selected_tokens and method='estimated'
* the metric is explicitly an *estimate*, not exact LLM-token usage
* the benchmark asserts that large fixtures do not end up in the
  context wholesale
* documentation names the limits of the metric
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.services.codecompass_context_planner_service import (
    CodeCompassContextPlanner,
)
from worker.retrieval.codecompass_review_context import (
    build_minimal_review_context,
)


TOKEN_ESTIMATE_VERSION = "context_savings.v1"
ESTIMATED_CHARS_PER_TOKEN = 4  # rough heuristic for English/ASCII


def estimate_tokens(payload: Any) -> int:
    """Crude estimate of token count: ``len(json) / 4``.

    The estimate is explicitly *approximate*; documented in
    docs/architecture/codecompass-import-trust.md. Production code
    must NOT advertise this as an exact LLM token count.
    """
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return max(1, len(blob) // ESTIMATED_CHARS_PER_TOKEN)


def compute_context_savings(
    *,
    planner: CodeCompassContextPlanner,
    query: str,
    task_kind: str | None,
    bucket_inputs: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Compute estimated_context_savings for a unified context package.

    The baseline is the *unbounded* size of all candidate items;
    selected_tokens is the post-budget size of the unified package.
    """
    if not bucket_inputs:
        # Without inputs we have no candidate set; we still report a
        # conservative baseline == selected == 0.
        package = planner.plan_unified_context(query=query, task_kind=task_kind,
                                                bucket_inputs=bucket_inputs)
        return {
            "estimated_context_savings": {
                "method": "estimated",
                "version": TOKEN_ESTIMATE_VERSION,
                "baseline_tokens": 0,
                "selected_tokens": estimate_tokens(package["buckets"]),
                "savings_pct": 0.0,
            },
            "package": package,
        }
    all_items = [item for items in bucket_inputs.values() for item in items]
    baseline_tokens = estimate_tokens(all_items)
    package = planner.plan_unified_context(query=query, task_kind=task_kind,
                                            bucket_inputs=bucket_inputs)
    selected_tokens = estimate_tokens(package["buckets"])
    savings_pct = (
        100.0 * (1.0 - selected_tokens / max(1, baseline_tokens))
        if baseline_tokens else 0.0
    )
    return {
        "estimated_context_savings": {
            "method": "estimated",
            "version": TOKEN_ESTIMATE_VERSION,
            "baseline_tokens": baseline_tokens,
            "selected_tokens": selected_tokens,
            "savings_pct": round(savings_pct, 2),
        },
        "package": package,
    }


def compute_review_context_savings(
    *,
    graph_store,
    changed_files: tuple[str, ...],
    seed_nodes: tuple[str, ...],
    task_kind: str = "review",
    include_repository_intelligence: bool = True,
) -> dict[str, Any]:
    """Same metric shape for the minimal-review-context path."""
    ctx = build_minimal_review_context(
        graph_store=graph_store,
        changed_files=changed_files,
        seed_nodes=seed_nodes,
        task_kind=task_kind,
        include_repository_intelligence=include_repository_intelligence,
    )
    ctx_payload = ctx.as_dict()
    # Baseline: assume each section's items would have been emitted in
    # full (no truncation). We approximate the baseline by treating each
    # section's items list as if it were unbounded.
    baseline_items = []
    for sec in ctx_payload["sections"]:
        baseline_items.extend(sec.get("items") or [])
    baseline_tokens = estimate_tokens(baseline_items)
    selected_tokens = estimate_tokens(ctx_payload["sections"])
    savings_pct = (100.0 * (1.0 - selected_tokens / max(1, baseline_tokens))
                   if baseline_tokens else 0.0)
    return {
        "estimated_context_savings": {
            "method": "estimated",
            "version": TOKEN_ESTIMATE_VERSION,
            "baseline_tokens": baseline_tokens,
            "selected_tokens": selected_tokens,
            "savings_pct": round(savings_pct, 2),
        },
        "review_context": ctx_payload,
    }


__all__ = [
    "TOKEN_ESTIMATE_VERSION",
    "ESTIMATED_CHARS_PER_TOKEN",
    "estimate_tokens",
    "compute_context_savings",
    "compute_review_context_savings",
]