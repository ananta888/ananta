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
            "new_manifest": dict(new_manifest),
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
        effective_layers: dict[str, str] = dict((head or {}).get("base_layer_set") or {})
        for delta in list((head or {}).get("ordered_delta_sets") or []):
            normalized = dict(delta) if isinstance(delta, dict) else {"default": str(delta)}
            effective_layers.update(normalized)
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
        append = decision["decision_type"] in {"delta_build", "metadata_only"}
        if not append:
            manifest_files = list((plan.get("new_manifest") or {}).get("files") or [])
            changes = [
                FileChange(
                    operation="add",
                    path=str(item.get("path") or ""),
                    new_content_sha256=str(item.get("content_sha256") or "") or None,
                    new_byte_size=item.get("byte_size"),
                )
                for item in manifest_files
                if item.get("path")
            ]
        last_layer = None
        for kind in kinds:
            parent = effective_layers.get(kind) if append else None
            layer = build_artifact_layer(
                changeset_id=str(diff.get("changeset_id") or ""),
                snapshot_revision=snapshot,
                parent_layer_id=parent,
                artifact_kind=kind,
                changes=changes,
                compatibility_key=compatibility_key(artifact_kind=kind, profile=profile),
                force_base=not append,
            )
            layer_id, _created = self.store.store_layer(layer)
            published[kind] = layer_id
            last_layer = layer_id
        if head is None:
            result = self.heads.create_head(
                profile_id,
                layer_id=last_layer or "",
                layer_set=published,
                snapshot_revision=snapshot,
                workspace_id=str(diff.get("workspace_id") or ""),
                repository_id=str(diff.get("repository_id") or ""),
            )
        else:
            result = self.heads.update_head(
                profile_id,
                expected_generation=int(head.get("generation") or 0),
                new_layer_id=last_layer or "",
                snapshot_revision=snapshot,
                append_delta=append,
                new_layer_set=published,
                replace_artifact_kinds=[] if append else kinds,
            )
            if not result.success:
                return {"status": "conflict", "error": result.error, "published": published}
        return {"status": "published", "layers": published, "head": self.heads.get_head(profile_id)}

    def compact(self, profile_id: str, *, dry_run: bool = True) -> dict[str, Any]:
        head = self.heads.get_head(profile_id) or {}
        chains: dict[str, list[str]] = {
            key: [str(value)] for key, value in dict(head.get("base_layer_set") or {}).items() if value
        }
        for delta in list(head.get("ordered_delta_sets") or []):
            normalized = dict(delta) if isinstance(delta, dict) else {"default": str(delta)}
            for kind, layer_id in normalized.items():
                if layer_id:
                    chains.setdefault(kind, []).append(str(layer_id))
        candidates = {kind: ids for kind, ids in chains.items() if len(ids) > 1}
        plan = self.planner.create_plan(profile_id, delta_ids=[item for ids in candidates.values() for item in ids])
        if dry_run or not candidates:
            return {"status": "noop" if not candidates else "planned", "plan": plan.to_dict()}
        published: dict[str, str] = {}
        for kind, chain in candidates.items():
            layers = [self.store.get_layer(item) for item in chain if self.store.has_layer(item)]
            compacted = self.planner.compact_layers([item for item in layers if item])
            layer_id, _ = self.store.store_layer(compacted)
            published[kind] = layer_id
        result = self.heads.update_head(
            profile_id,
            expected_generation=int(head.get("generation") or 0),
            new_layer_id=next(iter(published.values()), ""),
            new_layer_set=published,
            snapshot_revision=str(head.get("effective_source_revision") or ""),
            append_delta=False,
            replace_artifact_kinds=list(published),
            reason="compact",
        )
        return {"status": "executed" if result.success else "failed", "layers": published, "error": result.error}

    @staticmethod
    def equivalent(full_records: list[dict[str, Any]], layered: list[dict[str, Any]]) -> bool:
        left = {str(item.get("id")): item for item in overlay_records(full_records)}
        right = {str(item.get("id")): item for item in overlay_records(layered)}
        return left == right
