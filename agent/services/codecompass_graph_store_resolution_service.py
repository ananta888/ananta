"""Resolve a consumable CodeCompass graph artifact within an index scope."""

from __future__ import annotations

from typing import Any


def resolve_codecompass_graph_store(
    arguments: dict[str, Any] | None = None,
    *,
    allowed_index_ids: set[str] | None = None,
):
    from agent.services.codecompass_graph_artifact_resolver import (
        get_codecompass_graph_artifact_resolver,
    )
    from agent.services.knowledge_index_consumption_policy import (
        get_knowledge_index_consumption_policy,
    )
    from agent.services.repository_registry import get_repository_registry
    from ananta_codecompass.graph_store import CodeCompassGraphStore

    repo = get_repository_registry().knowledge_index_repo
    resolver = get_codecompass_graph_artifact_resolver()
    consumption_policy = get_knowledge_index_consumption_policy()
    requested = str((arguments or {}).get("knowledge_index_id") or "").strip()
    if requested:
        index = repo.get_by_id(requested)
        candidates = [index] if index is not None else []
    else:
        candidates = list(repo.list_completed() or [])
    diagnostics: list[dict[str, str]] = []
    for index in candidates:
        candidate_id = str(getattr(index, "id", "") or "")
        if not consumption_policy.can_consume(
            index, allowed_index_ids=allowed_index_ids
        ):
            diagnostics.append(
                {"knowledge_index_id": candidate_id, "reason": "consumption_denied"}
            )
            continue
        try:
            index_path, visual_metrics_path = resolver.resolve_artifacts(index)
            if not index_path.exists():
                index_path = resolver.resolve_legacy_tool_graph(index)
                visual_metrics_path = None
        except ValueError as exc:
            diagnostics.append(
                {
                    "knowledge_index_id": candidate_id,
                    "reason": str(exc)[:160] or "graph_artifact_invalid",
                }
            )
            continue
        if not index_path.exists():
            diagnostics.append(
                {"knowledge_index_id": candidate_id, "reason": "graph_file_missing"}
            )
            continue
        return (
            CodeCompassGraphStore(
                index_path=index_path,
                visual_metrics_path=visual_metrics_path,
            ),
            candidate_id,
            diagnostics[-8:],
        )
    if not candidates:
        diagnostics.append(
            {"knowledge_index_id": "", "reason": "no_completed_indices"}
        )
    return None, None, diagnostics[-8:]
