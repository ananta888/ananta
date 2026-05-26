from __future__ import annotations

from collections import defaultdict
from typing import Any

from agent.sources.citation_formatter import format_citation
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore

from .goal_artifact_service import GoalArtifactService


def _parse_source_ref(artifact_ref: str) -> tuple[str, str]:
    value = str(artifact_ref or "").strip()
    if value.startswith("sources:"):
        parts = value.split(":")
        if len(parts) >= 3:
            return parts[1], parts[2]
    return "unknown", "unknown"


class GoalCitationBundleService:
    def __init__(
        self,
        *,
        goal_artifact_service: GoalArtifactService | None = None,
        source_registry: SourceRegistry | None = None,
        source_snapshots: SourceSnapshotStore | None = None,
    ) -> None:
        self._goal_artifact_service = goal_artifact_service or GoalArtifactService()
        self._source_registry = source_registry or SourceRegistry()
        self._source_snapshots = source_snapshots or SourceSnapshotStore()

    def build_bundle(self, *, goal_id: str) -> dict[str, Any]:
        graph = self._goal_artifact_service.get_goal_graph(goal_id)
        usages = list(graph.get("source_usages") or [])
        outputs = list(graph.get("output_artifacts") or [])
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for usage in usages:
            source_id, snapshot_id = _parse_source_ref(str(usage.get("artifact_ref") or ""))
            grouped[(source_id, snapshot_id)].append(usage)

        citations: list[dict[str, Any]] = []
        for (source_id, snapshot_id), rows in grouped.items():
            output_refs = [
                str(item.get("artifact_ref") or "")
                for item in outputs
                if any(
                    str(ref) == str(usage.get("usage_id") or "")
                    for ref in list(item.get("input_usage_refs") or [])
                    for usage in rows
                )
            ]
            descriptor = self._source_registry.get_source(source_id) if source_id != "unknown" else None
            snapshot = None
            if source_id != "unknown" and snapshot_id != "unknown":
                for row in self._source_snapshots.list_snapshots(source_id=source_id):
                    if str(row.get("snapshot_id") or "") == snapshot_id:
                        snapshot = row
                        break
            if descriptor:
                rendered = format_citation(descriptor=descriptor, snapshot=snapshot, output_format="long")
                short = str(rendered.get("short") or "")
                long = str(rendered.get("long") or rendered.get("rendered") or "")
            else:
                short = f"{source_id}:{snapshot_id}"
                long = f"source={source_id} snapshot={snapshot_id}"
            citations.append(
                {
                    "source_id": source_id,
                    "snapshot_id": snapshot_id,
                    "short": short,
                    "long": long,
                    "usage_refs": [str(item.get("usage_id") or "") for item in rows],
                    "output_artifact_refs": sorted(set([ref for ref in output_refs if ref])),
                }
            )

        return {
            "goal_id": goal_id,
            "citation_count": len(citations),
            "citations": sorted(
                citations,
                key=lambda row: (
                    str(row.get("source_id") or ""),
                    str(row.get("snapshot_id") or ""),
                ),
            ),
        }
