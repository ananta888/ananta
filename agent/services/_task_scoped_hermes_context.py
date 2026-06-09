"""Hermes-context-block builder, extracted from task_scoped_execution_service.

SPLIT-001k: owns the single concern of translating a task + request +
research context into a list of typed ContextBlocks for HermesAdapter.

Backwards compatibility: the module-level symbol
``build_hermes_context_blocks`` is re-exported from
``agent.services.task_scoped_execution_service`` (12-month deprecation
window, see todos/todo.refactor-large-files-split.json SPLIT-001).
"""

from __future__ import annotations


def build_hermes_context_blocks(
    *,
    task: dict,
    request_data: object,
    research_context: object,
) -> list:
    """Build ContextBlock list from task + research context for HermesAdapter. HF-T020."""
    from worker.core.context_resolver import ContextBlock, ContextSensitivity

    blocks: list[ContextBlock] = []

    # Task description / prompt (P0 — never dropped)
    task_description = str(
        getattr(request_data, "prompt", None)
        or (task or {}).get("description")
        or ""
    ).strip()
    if task_description:
        blocks.append(ContextBlock(
            source_type="task_description",
            origin_id=str((task or {}).get("id") or "task"),
            provenance="_task_scoped_hermes_context:task_description",
            sensitivity=ContextSensitivity.project_internal,
            content=task_description,
            priority=0,
        ))

    # Research context prompt section
    rc = research_context if isinstance(research_context, dict) else {}
    prompt_section = str(rc.get("prompt_section") or "").strip()
    if prompt_section:
        blocks.append(ContextBlock(
            source_type="research_context",
            origin_id="research_context:prompt_section",
            provenance="_task_scoped_hermes_context:research_context",
            sensitivity=ContextSensitivity.project_internal,
            content=prompt_section,
            priority=10,
        ))

    # Additional context from request_data.context_blocks if present
    raw_blocks = getattr(request_data, "context_blocks", None) or []
    for idx, raw in enumerate(raw_blocks if isinstance(raw_blocks, list) else []):
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        sensitivity_raw = str(raw.get("sensitivity") or ContextSensitivity.project_internal.value)
        try:
            sensitivity = ContextSensitivity(sensitivity_raw)
        except ValueError:
            sensitivity = ContextSensitivity.project_internal
        blocks.append(ContextBlock(
            source_type=str(raw.get("source_type") or "external_context"),
            origin_id=str(raw.get("origin_id") or f"context_block_{idx}"),
            provenance="_task_scoped_hermes_context:request_context_blocks",
            sensitivity=sensitivity,
            content=content,
            priority=int(raw.get("priority") or 50),
        ))

    return blocks
