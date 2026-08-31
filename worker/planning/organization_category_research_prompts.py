"""Bounded prompt projection for organization-category research workers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from worker.planning.organization_category_research_contracts import (
    OrganizationCategoryResearchExecutionError,
)

_PROMPT_CONTEXT_MAX_CHARS = 20_000
_PROMPT_SOURCE_LABEL_MAX_CHARS = 64


def prompt_context(chunks: list[dict[str, Any]]) -> str:
    """Project verified chunks into a bounded, reference-preserving view."""

    by_source_ref: dict[str, tuple[str, str]] = {}
    for chunk in chunks:
        metadata = dict(chunk.get("metadata")) if isinstance(chunk.get("metadata"), Mapping) else {}
        source_ref = str(chunk.get("source_id") or metadata.get("source_id") or "").strip()
        if not source_ref or source_ref in by_source_ref:
            continue
        source_label = str(
            chunk.get("source")
            or metadata.get("source")
            or metadata.get("path")
            or metadata.get("file_path")
            or "source"
        ).strip()[:_PROMPT_SOURCE_LABEL_MAX_CHARS]
        by_source_ref[source_ref] = (source_label, str(chunk.get("content") or "").strip())

    entries = sorted(by_source_ref.items())
    if not entries:
        return ""
    headers = [f"[{source_ref}] {source_label}" for source_ref, (source_label, _content) in entries]
    separator_chars = max(0, len(entries) - 1) * 2
    fixed_chars = sum(len(header) + 1 for header in headers) + separator_chars
    if fixed_chars > _PROMPT_CONTEXT_MAX_CHARS:
        headers = [f"[{source_ref}]" for source_ref, _value in entries]
        fixed_chars = sum(len(header) + 1 for header in headers) + separator_chars
    if fixed_chars > _PROMPT_CONTEXT_MAX_CHARS:
        raise OrganizationCategoryResearchExecutionError("category_research_prompt_projection_too_large")

    remaining = _PROMPT_CONTEXT_MAX_CHARS - fixed_chars
    excerpt_chars, extra_chars = divmod(remaining, len(entries))
    projected: list[str] = []
    for index, (header, (_source_ref, (_label, content))) in enumerate(zip(headers, entries, strict=True)):
        allowance = excerpt_chars + (1 if index < extra_chars else 0)
        projected.append(f"{header}\n{content[:allowance]}")
    return "\n\n".join(projected)


def build_prompt(
    *,
    task: Mapping[str, Any],
    context_text: str,
    source_catalog: Mapping[str, Any],
    source_catalog_id: str,
    source_catalog_hash: str,
    allowed_source_refs: tuple[str, ...],
    allowed_run_refs: tuple[str, ...],
    repository_revision: str,
) -> str:
    current_date = datetime.now(timezone.utc).date().isoformat()
    source_claim_id = "CLM_0001"
    run_claim_id = "CLM_0002"
    item_id = "HRM-RESEARCH-REVIEW-001"
    draft = {
        "version": "1",
        "created": current_date,
        "updated": current_date,
        "project": "Ananta HRM experiment research",
        "review_basis": {
            "reviewed_commit_range": repository_revision,
            "review_goal": "Review the governed HRM experiment research pack.",
        },
        "categories": [{
            "name": "hrm-experiment-research",
            "label": "HRM experiment research",
            "items": [{
                "id": item_id,
                "title": "Review the governed HRM experiment research pack",
                "status": "open",
                "priority": "high",
                "risk": "medium",
                "type": "research",
                "depends_on": [],
                "acceptance_criteria": [
                    "Every proposed HRM experiment decision is checked against the assignment-bound source context.",
                    (
                        "Factual decisions cite only assignment-allowed source references "
                        "and the execution receipt cites the allowed run reference."
                    ),
                ],
                "evidence_claim_refs": [source_claim_id, run_claim_id],
                "source_citation_refs": list(allowed_source_refs),
                "evidence_summary": (
                    "The review is scoped to the exact Hub-issued source catalog and repository revision."
                ),
            }],
        }],
        "meta": {
            "total_items": 1,
            "by_status": {"completed": 0, "partial": 0, "open": 1},
            "notes": ["The Hub remains the owner of assignment and promotion."],
            "recommended_order": [item_id],
        },
        "planning_quality_profile": {
            "schema": "category_todo_quality_profile.v1",
            "source_catalog_id": source_catalog_id,
            "source_catalog_hash": source_catalog_hash,
            "allowed_source_refs": list(allowed_source_refs),
            "allowed_run_refs": list(allowed_run_refs),
            "research_summary": "The assignment supplies a governed HRM source catalog for review.",
            "claims": [
                {
                    "claim_id": source_claim_id,
                    "text": (
                        "The Hub supplied the assignment-scoped HRM source catalog and repository revision "
                        "used by this review."
                    ),
                    "claim_type": "source_fact",
                    "citation_refs": list(allowed_source_refs),
                    "confidence": "verified",
                },
                {
                    "claim_id": run_claim_id,
                    "text": "This artifact was returned by the assignment-bound Worker execution.",
                    "claim_type": "tool_result",
                    "citation_refs": list(allowed_run_refs),
                    "confidence": "verified",
                },
            ],
            "unsupported_notes": [],
            "grounding_status": "verified",
            "grounding_reason": "Every claim is limited to the exact assignment allowlists.",
        },
    }
    return "\n".join((
        "Execute one Hub-delegated planning_research assignment.",
        "Return the DRAFT JSON below exactly, with no Markdown or prose.",
        "Do not add, remove, rename, or rewrite any JSON value.",
        "Do not modify files, run tools, create tasks, or orchestrate workers.",
        "Never invent SRC_* or RUN_* identifiers.",
        f"Task: {str(task.get('description') or task.get('title') or '').strip()}",
        "SOURCE CATALOG IDENTITY:\n" + json.dumps({
            "schema": source_catalog.get("schema"),
            "source_catalog_id": source_catalog_id,
            "source_catalog_hash": source_catalog_hash,
            "source_ids": list(allowed_source_refs),
        }, ensure_ascii=False, sort_keys=True),
        "DRAFT JSON:\n" + json.dumps(draft, ensure_ascii=False, separators=(",", ":")),
        "SOURCE CONTEXT:\n" + context_text,
    ))


def repair_prompt(*, prompt: str, raw_output: str, issues: list[str]) -> str:
    return (
        prompt
        + "\n\nREPAIR REQUIRED. Return a corrected complete JSON object only."
        + "\nValidation issues: "
        + json.dumps(issues[:30])
        + "\nPrevious output:\n"
        + str(raw_output or "")[:60000]
    )
