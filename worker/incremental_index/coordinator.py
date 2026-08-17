"""Plan and apply incremental layer updates without mixing artifact heads."""

from __future__ import annotations

from typing import Any

from worker.incremental_index.builders import build_artifact_layer
from worker.incremental_index.compaction import CompactionPlanner
from worker.incremental_index.compatibility import compatibility_key
from worker.incremental_index.decision_engine import IncrementalBuildDecisionEngine
from worker.incremental_index.dependency_impact import DependencyImpactAnalyzer
from worker.incremental_index.effective_view import overlay_records
from worker.incremental_index.head_registry import LayerHeadRegistry
from worker.incremental_index.layer_store import ArtifactLayerStore
from worker.incremental_index.snapshot_diff import SnapshotDiffResult, diff_snapshots


class IncrementalIndexCoordinator:
    def __init__(self, base_path) -> None:
        self.store = ArtifactLayerStore(base_path)
        self.heads = LayerHeadRegistry(base_path)
        self.engine = IncrementalBuildDecisionEngine()
        self.planner = CompactionPlanner(str(base_path))

    def plan(
        self,
        *,
        old_manifest: dict[str, Any],
        new_manifest: dict[str, Any],
        profile: dict[str, Any],
        previous_profile: dict[str, Any] | None = None,
        workspace_id: str | None = None,
        repository_id: str | None = None,
        profile_id: str = "default",
        symbol_graph: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        diff = diff_snapshots(old_manifest, new_manifest, workspace_id, repository_id)
        impact = DependencyImpactAnalyzer(symbol_graph).analyze_impact(diff.changed_paths, diff.changeset_id)
        head = self.heads.get_head(profile_id) or {}
        delta_depth = len(list(head.get("ordered_delta_sets") or []))
        decision = self.engine.decide(
            changeset_size=len(diff.file_changes),
            impact_result=impact.to_dict(),
            build_profile_old=previous_profile or profile,
            build_profile_new=profile,
            delta_depth=delta_depth,
        )
        return {
            "changeset": diff.to_dict(),
            "impact": impact.to_dict(),
            "decision": decision.to_dict(),
            "head": head,
        }

    def apply(
        self,
        *,
        plan: dict[str, Any],
        profile: dict[str, Any],
        profile_id: str = "default",
        artifact_kinds: list[str] | None = None,
    ) -> dict[str, Any]:
        decision = plan["decision"]
        diff = plan["changeset"]
        if decision["decision_type"] == "noop":
            return {"status": "noop", "head": self.heads.get_head(profile_id)}
        kinds = list(artifact_kinds or decision.get("affected_artifact_kinds") or ["graph", "chunks", "embeddings", "fts"])
        head = self.heads.get_head(profile_id)
        parent = None if not head else str(head.get("layer_id") or "")
        published: dict[str, str] = {}
        from worker.incremental_index.snapshot_diff import FileChange

        changes = [
            FileChange(
                operation=item["operation"],
                path=item["path"],
                new_path=item.get("new_path"),
                old_content_sha256=item.get("old_content_sha256"),
                new_content_sha256=item.get("new_content_sha256"),
            )
            for item in list(diff.get("file_changes") or [])
        ]
        snapshot = str(diff.get("to_snapshot_revision") or "")
        last_layer = parent
        for kind in kinds:
            layer = build_artifact_layer(
                changeset_id=str(diff.get("changeset_id") or ""),
                snapshot_revision=snapshot,
                parent_layer_id=parent,
                artifact_kind=kind,
                changes=changes,
                compatibility_key=compatibility_key(artifact_kind=kind, profile=profile),
            )
            layer_id, _created = self.store.store_layer(layer)
            published[kind] = layer_id
            last_layer = layer_id
        if head is None:
            result = self.heads.create_head(profile_id, layer_id=last_layer or "", snapshot_revision=snapshot)
        else:
            append = decision["decision_type"] == "delta_build"
            result = self.heads.update_head(
                profile_id,
                expected_generation=int(head.get("generation") or 0),
                new_layer_id=last_layer or "",
                snapshot_revision=snapshot,
                append_delta=append,
            )
            if not result.success:
                return {"status": "conflict", "error": result.error, "published": published}
        return {"status": "published", "layers": published, "head": self.heads.get_head(profile_id)}

    def compact(self, profile_id: str, *, dry_run: bool = True) -> dict[str, Any]:
        head = self.heads.get_head(profile_id) or {}
        chain = [str((head.get("base_layer_set") or {}).get("default") or head.get("layer_id") or "")]
        chain.extend(str(item) for item in list(head.get("ordered_delta_sets") or []))
        layers = [self.store.get_layer(item) for item in chain if item and self.store.has_layer(item)]
        plan = self.planner.create_plan(profile_id, delta_ids=[item for item in chain if item])
        if dry_run or not layers:
            return {"status": "noop" if len(layers) <= 1 else "planned", "plan": plan.to_dict()}
        compacted = self.planner.compact_layers([item for item in layers if item])
        layer_id, _ = self.store.store_layer(compacted)
        result = self.heads.update_head(
            profile_id,
            expected_generation=int(head.get("generation") or 0),
            new_layer_id=layer_id,
            snapshot_revision=str(head.get("effective_source_revision") or ""),
            append_delta=False,
            reason="compact",
        )
        return {"status": "executed" if result.success else "failed", "layer_id": layer_id, "error": result.error}

    @staticmethod
    def equivalent(full_records: list[dict[str, Any]], layered: list[dict[str, Any]]) -> bool:
        left = {str(item.get("id")): item for item in overlay_records(full_records)}
        right = {str(item.get("id")): item for item in overlay_records(layered)}
        return set(left) == set(right)
