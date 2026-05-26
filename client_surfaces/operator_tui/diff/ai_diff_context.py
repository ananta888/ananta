from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent.artifacts.artifact_grants import is_grant_active
from agent.artifacts.goal_artifact_service import GoalArtifactService
from client_surfaces.operator_tui.diff.diff_source_resolver import DiffSourceResolver


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_ai_diff_context_envelope(
    *,
    diff3_state: dict[str, Any],
    goal_id: str | None,
    max_context_chars: int = 3000,
    resolver: DiffSourceResolver | None = None,
    goal_artifact_service: GoalArtifactService | None = None,
) -> dict[str, Any]:
    service = goal_artifact_service or GoalArtifactService()
    resolve = resolver or DiffSourceResolver(goal_artifact_service=service)
    extensions = dict(diff3_state.get("extensions") or {})
    ai_state = dict(extensions.get("ai_panel_state") or {})
    selected_panels = [str(item) for item in list(ai_state.get("selected_panels") or ["A", "B"]) if str(item) in {"A", "B", "C"}]
    selected_hunks = [str(item) for item in list(ai_state.get("selected_hunks") or []) if str(item).strip()]
    rows = [dict(item) for item in list(diff3_state.get("panels") or [])]
    by_panel = {str(item.get("panel_id") or ""): item for item in rows}
    diff_source_refs: list[dict[str, Any]] = []
    selected_file_refs: list[str] = []
    artifact_refs: list[str] = []
    denied_context_refs: list[str] = []
    summary_parts: list[str] = []
    consumed = 0
    truncated = False

    active_grants: set[str] = set()
    if goal_id:
        graph = service.get_goal_graph(str(goal_id))
        for grant in list(graph.get("source_grants") or []):
            ok, _ = is_grant_active(dict(grant))
            if ok:
                active_grants.add(str(grant.get("artifact_ref") or ""))

    for panel_id in selected_panels:
        panel = by_panel.get(panel_id) or {}
        source = panel.get("source_left")
        if not isinstance(source, dict):
            continue
        diff_source_refs.append(dict(source))
        filters = dict(panel.get("filters") or {})
        if str(filters.get("path_filter") or "").strip():
            selected_file_refs.append(str(filters["path_filter"]).strip())
        source_kind = str(source.get("source_kind") or "")
        locator = dict(source.get("locator") or {})
        if source_kind == "artifact_ref":
            artifact_ref = str(locator.get("artifact_ref") or "").strip()
            if artifact_ref:
                artifact_refs.append(artifact_ref)
                if goal_id and artifact_ref not in active_grants:
                    denied_context_refs.append(artifact_ref)
        resolved = resolve.resolve(source, goal_id=goal_id)
        if not bool(resolved.get("ok")):
            continue
        excerpt = ""
        if resolved.get("content_type") == "patch":
            excerpt = str(resolved.get("patch") or "")
        elif resolved.get("content_type") == "text":
            excerpt = str(resolved.get("text") or "")
        elif resolved.get("content_type") == "pair":
            excerpt = f"{resolved.get('left_text')}\n---\n{resolved.get('right_text')}"
        if not excerpt:
            continue
        available = max_context_chars - consumed
        if available <= 0:
            truncated = True
            break
        if len(excerpt) > available:
            summary_parts.append(excerpt[:available])
            consumed += available
            truncated = True
            break
        summary_parts.append(excerpt)
        consumed += len(excerpt)

    return {
        "schema": "ai_diff_context_envelope.v1",
        "goal_id": goal_id or None,
        "diff_source_refs": diff_source_refs,
        "selected_file_refs": sorted(set(selected_file_refs)),
        "selected_hunk_refs": sorted(set(selected_hunks)),
        "artifact_refs": sorted(set(artifact_refs)),
        "denied_context_refs": sorted(set(denied_context_refs)),
        "diff_summary": "\n".join(summary_parts),
        "truncated": truncated,
        "context_budget_chars": max_context_chars,
        "created_at": _now_iso(),
    }

