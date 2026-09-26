"""Read and planning side of Hub-owned incremental CodeCompass ``chunks`` layers.

Implements the query half of ``CodeCompassLayerBackendPort``: heads, diffs
and update plans. A plan names exactly what a Worker needs to build one
layer: the file changes, the parent layer, and the record ids the effective
view currently holds for every changed path (so the Worker can tombstone
chunks that disappear). Building, dispatching and publishing are not done
here; ``apply_update`` and ``admit_result`` belong to the dispatch backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.services.codecompass_layer_hub_store import SnapshotManifestStore
from worker.incremental_index.effective_view import LayeredEffectiveViewResolver
from worker.incremental_index.head_registry import LayerHeadRegistry
from worker.incremental_index.layer_store import ArtifactLayerStore
from worker.incremental_index.snapshot_diff import diff_snapshots

ARTIFACT_KIND = "chunks"
PLAN_SCHEMA = "ananta.codecompass_layer_plan.v1"
EMPTY_SNAPSHOT = {"snapshot_revision": "0" * 64, "files": []}


@dataclass(frozen=True)
class ChunkPlanPolicy:
    """When a chunks update is a delta and when it starts a new base.

    Chunk records depend only on their own file, so any compatible change is
    a valid delta; a new base is only needed without a head, after a
    compatibility change, or when the delta chain got too deep.
    """

    max_delta_depth: int = 64

    def decide(self, *, has_head: bool, changes: int, delta_depth: int, compatible: bool) -> tuple[str, str]:
        if has_head and compatible and changes == 0:
            return "noop", "no_changes"
        if not has_head:
            return "base_build", "no_head"
        if not compatible:
            return "base_build", "compatibility_changed"
        if delta_depth >= self.max_delta_depth:
            return "base_build", "delta_depth"
        return "delta_build", "compatible_change"


def indexed_view(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The manifest restricted to indexed files: anything else must not be in the index."""
    files = [
        dict(item)
        for item in list(manifest.get("files") or [])
        if isinstance(item, Mapping) and item.get("path") and str(item.get("outcome") or "indexed") == "indexed"
    ]
    return {**dict(manifest), "files": files}


class CodeCompassLayerQueryBackend:
    """Heads, diffs and plans over the Hub's layer store (no writes)."""

    def __init__(
        self,
        *,
        layers: ArtifactLayerStore,
        heads: LayerHeadRegistry,
        snapshots: SnapshotManifestStore,
        policy: ChunkPlanPolicy | None = None,
    ) -> None:
        self._layers = layers
        self._heads = heads
        self._snapshots = snapshots
        self._policy = policy or ChunkPlanPolicy()

    # --- read ----------------------------------------------------------------------

    def list_profiles(self) -> list[str]:
        return self._heads.list_profiles()

    def show_head(self, profile_id: str) -> dict[str, Any] | None:
        return self._heads.get_head(str(profile_id))

    def diff(self, **kwargs: Any) -> dict[str, Any]:
        old = self._manifest(kwargs.get("from_snapshot_ref"), kwargs.get("old_manifest"))
        new = self._manifest(kwargs.get("to_snapshot_ref"), kwargs.get("new_manifest"))
        return diff_snapshots(indexed_view(old), indexed_view(new)).to_dict()

    def effective_record_ids_by_path(self, profile_id: str) -> dict[str, list[str]]:
        view = LayeredEffectiveViewResolver(self._layers, self._heads).resolve_effective_view(
            profile_id, artifact_types=[ARTIFACT_KIND]
        )
        by_path: dict[str, list[str]] = {}
        for record_id, artifact in view.artifacts.items():
            by_path.setdefault(str(artifact.metadata.get("path") or ""), []).append(record_id)
        return {path: sorted(ids) for path, ids in by_path.items()}

    # --- plan ----------------------------------------------------------------------

    def plan_update(self, **kwargs: Any) -> dict[str, Any]:
        profile_id = str(kwargs.get("profile_id") or "")
        if not profile_id:
            raise ValueError("codecompass_layer_profile_id_required")
        head = self._heads.get_head(profile_id)
        new = indexed_view(self._manifest(kwargs.get("to_snapshot_ref"), kwargs.get("new_manifest")))
        head_manifest = self._head_manifest(head)
        compatibility = dict(kwargs.get("compatibility_key") or {})
        compatible = head is None or dict(head.get("compatibility_key") or {}) == compatibility
        depth = len(list((head or {}).get("ordered_delta_sets") or []))

        changes = diff_snapshots(indexed_view(head_manifest), new).to_dict()
        decision, reason = self._policy.decide(
            has_head=head is not None,
            changes=len(changes["file_changes"]),
            delta_depth=depth,
            compatible=compatible,
        )
        if decision == "base_build" and head is not None:
            # A new base holds every indexed file; nothing is inherited from the chain.
            changes = diff_snapshots(EMPTY_SNAPSHOT, new).to_dict()
        prior = self._prior_ids(profile_id, changes) if decision == "delta_build" else {}
        return {
            "schema": PLAN_SCHEMA,
            "profile_id": profile_id,
            "input_revision": str(new["snapshot_revision"]),
            "from_revision": str(head_manifest["snapshot_revision"]),
            "artifact_kinds": [ARTIFACT_KIND],
            "decision": {"decision_type": decision, "reason": reason, "delta_depth": depth},
            "head_generation": int((head or {}).get("generation") or 0),
            "parent_layer_id": self._parent_layer_id(head) if decision == "delta_build" else None,
            "compatibility_key": compatibility,
            "changeset": changes,
            "prior_record_ids_by_path": prior,
            "content_sha256": sorted(
                {
                    str(item.get("new_content_sha256"))
                    for item in changes["file_changes"]
                    if item.get("operation") != "delete" and item.get("new_content_sha256")
                }
            ),
        }

    def compact(self, **kwargs: Any) -> dict[str, Any]:
        profile_id = str(kwargs.get("profile_id") or "")
        head = self._heads.get_head(profile_id) or {}
        chain = self._chain(head)
        return {
            "schema": PLAN_SCHEMA,
            "profile_id": profile_id,
            "status": "planned" if len(chain) > 1 else "noop",
            "input_revision": str(head.get("effective_source_revision") or ""),
            "artifact_kinds": [ARTIFACT_KIND],
            "layer_chain": chain,
            "head_generation": int(head.get("generation") or 0),
        }

    def apply_update(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("codecompass_layer_query_backend_read_only")

    def admit_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        raise RuntimeError("codecompass_layer_query_backend_read_only")

    # --- helpers --------------------------------------------------------------------

    def _manifest(self, ref: Any, inline: Any) -> dict[str, Any]:
        if isinstance(inline, Mapping) and inline:
            return dict(inline)
        if ref in (None, "", "empty"):
            return dict(EMPTY_SNAPSHOT)
        manifest = self._snapshots.get(str(ref))
        if manifest is None:
            raise KeyError(f"codecompass_snapshot_unknown:{ref}")
        return manifest

    def _head_manifest(self, head: Mapping[str, Any] | None) -> dict[str, Any]:
        if not head:
            return dict(EMPTY_SNAPSHOT)
        manifest = self._snapshots.get(str(head.get("effective_source_revision") or ""))
        if manifest is None:
            raise KeyError("codecompass_head_snapshot_missing")
        return manifest

    def _prior_ids(self, profile_id: str, changes: Mapping[str, Any]) -> dict[str, list[str]]:
        effective = self.effective_record_ids_by_path(profile_id)
        paths: set[str] = set()
        for item in changes["file_changes"]:
            paths.add(str(item.get("path") or ""))
            if item.get("new_path"):
                paths.add(str(item["new_path"]))
        return {path: effective[path] for path in sorted(paths) if path in effective}

    @staticmethod
    def _chain(head: Mapping[str, Any]) -> list[str]:
        chain = [str(value) for _key, value in sorted(dict(head.get("base_layer_set") or {}).items()) if value]
        for delta in list(head.get("ordered_delta_sets") or []):
            normalized = dict(delta) if isinstance(delta, Mapping) else {"default": str(delta)}
            chain.extend(str(value) for _key, value in sorted(normalized.items()) if value)
        return chain

    def _parent_layer_id(self, head: Mapping[str, Any] | None) -> str | None:
        chain = self._chain(head or {})
        return chain[-1] if chain else None
