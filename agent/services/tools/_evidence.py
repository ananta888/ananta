"""AWTCL-007: evidence model for ToolResults of the ananta-worker tool loop.

ToolResults carry their findings as structured evidence entries (file
line ranges, snippets, graph paths, test output excerpts). Large payloads
are truncated deterministically and marked with a truncation warning so
every worker answer can reference bounded, reproducible evidence.

Contract: ``docs/contracts/ananta-worker-tool-loop.md``
(``ananta_tool_result.v1``).
"""
from __future__ import annotations

from typing import Any

TOOL_RESULT_SCHEMA = "ananta_tool_result.v1"

EVIDENCE_KIND_FILE_EXCERPT = "file_excerpt"
EVIDENCE_KIND_FILE_LIST = "file_list"
EVIDENCE_KIND_GREP_MATCH = "grep_match"
EVIDENCE_KIND_RETRIEVAL_CHUNK = "retrieval_chunk"
EVIDENCE_KIND_GRAPH_PATH = "graph_path"
EVIDENCE_KIND_TEST_OUTPUT = "test_output"
EVIDENCE_KIND_DIFF = "diff"
EVIDENCE_KIND_POLICY = "policy_result"

TRUNCATION_WARNING = "evidence_truncated"


def truncate_excerpt(text: str | None, limit: int) -> tuple[str, bool]:
    value = str(text or "")
    if limit <= 0 or len(value) <= limit:
        return value, False
    return value[: max(1, limit - 12)].rstrip() + "\n[truncated]", True


def build_evidence_entry(
    *,
    kind: str,
    path: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    excerpt: str = "",
    score: float | None = None,
    source: str | None = None,
    max_excerpt_chars: int = 2000,
) -> tuple[dict[str, Any], bool]:
    """Build one bounded evidence entry; returns (entry, was_truncated)."""
    bounded, truncated = truncate_excerpt(excerpt, max_excerpt_chars)
    entry: dict[str, Any] = {"kind": kind, "excerpt": bounded}
    if path is not None:
        entry["path"] = str(path)
    if line_start is not None:
        entry["line_start"] = int(line_start)
    if line_end is not None:
        entry["line_end"] = int(line_end)
    if score is not None:
        entry["score"] = float(score)
    if source is not None:
        entry["source"] = str(source)
    if truncated:
        entry["truncated"] = True
    return entry, truncated


def build_tool_result(
    *,
    tool_name: str,
    tool_call_id: str,
    status: str,
    risk_class: str = "low",
    evidence: list[dict[str, Any]] | None = None,
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    error: str | None = None,
    policy_decision: dict[str, Any] | None = None,
    max_total_chars: int | None = None,
) -> dict[str, Any]:
    """Assemble one ``ananta_tool_result.v1`` payload with a total size cap.

    ``max_total_chars`` bounds the serialized evidence excerpts; surplus
    entries are dropped and the result gets a truncation warning so the
    next LLM round knows the answer is partial.
    """
    rows = list(evidence or [])
    result_warnings = list(warnings or [])
    if max_total_chars and max_total_chars > 0:
        kept: list[dict[str, Any]] = []
        used = 0
        for row in rows:
            cost = len(str(row.get("excerpt") or "")) + 100
            if used + cost > max_total_chars and kept:
                result_warnings.append(TRUNCATION_WARNING)
                break
            used += cost
            kept.append(row)
        rows = kept
    if any(bool(row.get("truncated")) for row in rows) and TRUNCATION_WARNING not in result_warnings:
        result_warnings.append(TRUNCATION_WARNING)
    payload: dict[str, Any] = {
        "schema": TOOL_RESULT_SCHEMA,
        "tool_call_id": str(tool_call_id),
        "tool_name": str(tool_name),
        "status": str(status),
        "risk_class": str(risk_class),
        "evidence": rows,
        "warnings": sorted(set(result_warnings)),
    }
    if data:
        payload["data"] = data
    if error:
        payload["error"] = str(error)
    if policy_decision:
        payload["policy_decision"] = dict(policy_decision)
    return payload
