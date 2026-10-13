"""Scoped, read-only layer observations; no build, dispatch or head mutation."""

from copy import deepcopy

from agent.services.codecompass_layer_publication_observers import pointer_index_id
from agent.services.codecompass_retrieval_capability_service import verify_retrieval_capability
from agent.services.codecompass_scoped_graph import path_allowed
from agent.services.knowledge_index_consumption_policy import (
    CODECOMPASS_LAYER_POINTER_KEY,
    CODECOMPASS_LAYER_POINTER_SCHEMA,
)


class FrozenHeads:
    def __init__(self, profile, head):
        self.profile, self.head = profile, deepcopy(head)

    def get_head(self, profile):
        if profile != self.profile:
            raise ValueError("codecompass_layer_profile_denied")
        return deepcopy(self.head)


class ScopedSnapshots:
    def __init__(self, manifests, paths):
        self.manifests, self.paths = deepcopy(manifests), paths

    def get(self, revision):
        manifest = deepcopy(self.manifests.get(revision))
        if manifest is not None:
            manifest["files"] = [row for row in manifest.get("files", [])
                                 if isinstance(row, dict) and path_allowed(row.get("path"), self.paths)]
        return manifest


class CodeCompassScopedLayers:
    def __init__(self, capability, *, layers, heads, snapshots, indices, policy):
        self.scope = verify_retrieval_capability(capability)
        self.layers, self.heads, self.snapshots, self.indices, self.policy = layers, heads, snapshots, indices, policy

    def list_profiles(self):
        verify_retrieval_capability(self.scope)
        profiles = []
        # Resolve only explicitly authorized Hub pointer rows, never enumerate
        # the global profile catalog to construct authority.
        for index_id in self.scope["allowed_index_ids"]:
            row = self.indices.get_by_id(index_id)
            metadata = getattr(row, "index_metadata", {}) or {}
            if not isinstance(metadata, dict):
                continue
            pointer = metadata.get(CODECOMPASS_LAYER_POINTER_KEY)
            if (getattr(row, "status", None) != "completed" or not isinstance(pointer, dict)
                    or pointer.get("schema") != CODECOMPASS_LAYER_POINTER_SCHEMA):
                continue
            profile = pointer.get("profile_id")
            if isinstance(profile, str) and profile and pointer_index_id(profile) == index_id:
                profiles.append(profile)
        return sorted(set(profiles))

    def _head(self, profile):
        if profile not in self.list_profiles():
            raise ValueError("codecompass_layer_profile_denied")
        head = deepcopy(self.heads.get_head(profile))
        if head is not None:
            if head.get("profile_id") != profile or any(
                head.get(field) and head[field] != self.scope[field] for field in ("workspace_id", "repository_id")
            ):
                raise ValueError("codecompass_layer_scope_changed")
        return head

    def show_head(self, profile):
        head = self._head(profile)
        if head is None:
            return None
        # Do not expose arbitrary compatibility metadata or publication history.
        return {key: deepcopy(head[key]) for key in (
            "schema", "profile_id", "generation", "artifact_set_revision", "effective_source_revision",
            "base_layer_set", "ordered_delta_sets", "layer_id",
        ) if key in head}

    def _manifest(self, supplied, revision):
        stored = self.snapshots.get(revision)
        if not isinstance(stored, dict) or stored.get("snapshot_revision") != revision:
            raise ValueError("codecompass_layer_snapshot_unavailable")
        # A closed reference is additive; complete legacy inline manifests must
        # equal the Hub's admitted bytes, never replace them with model input.
        if supplied and supplied not in ({"snapshot_revision": revision}, stored):
            raise ValueError("codecompass_layer_snapshot_mismatch")
        return deepcopy(stored)

    def plan_update(self, *, profile_id, old_manifest, new_manifest):
        from agent.services.codecompass_layer_query_backend import EMPTY_SNAPSHOT, CodeCompassLayerQueryBackend
        from agent.services.meet_companion_task_tools import digest

        head = self._head(profile_id)
        new = self._manifest(new_manifest, self.scope["revision"])
        old_revision = (head or {}).get("effective_source_revision")
        old = self._manifest(old_manifest, old_revision) if old_revision else deepcopy(EMPTY_SNAPSHOT)
        if not old_revision and old_manifest and old_manifest != EMPTY_SNAPSHOT:
            raise ValueError("codecompass_layer_snapshot_mismatch")
        manifests = ScopedSnapshots({old["snapshot_revision"]: old, new["snapshot_revision"]: new},
                                    self.scope["allowed_paths"])
        query = CodeCompassLayerQueryBackend(layers=self.layers, heads=FrozenHeads(profile_id, head),
                                            snapshots=manifests, policy=self.policy)
        compatibility = deepcopy((head or {}).get("compatibility_key") or {})
        plan = query.plan_update(profile_id=profile_id, to_snapshot_ref=new["snapshot_revision"],
                                 compatibility_key=compatibility)
        plan["compatibility_key"] = {"digest": digest(compatibility)}
        if self._head(profile_id) != head:
            raise ValueError("codecompass_layer_head_changed")
        return plan
