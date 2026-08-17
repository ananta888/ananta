"""Hub facade for CodeCompass incremental layer heads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from worker.incremental_index.coordinator import IncrementalIndexCoordinator
from worker.incremental_index.garbage_collector import GarbageCollector


def default_layer_root() -> Path:
    return Path("data") / "codecompass-layers"


class CodeCompassLayerService:
    def __init__(self, root: str | Path | None = None) -> None:
        self.coordinator = IncrementalIndexCoordinator(root or default_layer_root())

    def list_profiles(self) -> list[str]:
        return self.coordinator.heads.list_profiles()

    def show_head(self, profile_id: str) -> dict[str, Any] | None:
        return self.coordinator.heads.get_head(profile_id)

    def diff(self, old_manifest: dict[str, Any], new_manifest: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self.coordinator.plan(old_manifest=old_manifest, new_manifest=new_manifest, profile=kwargs.get("profile") or {}, **{k: v for k, v in kwargs.items() if k != "profile"})["changeset"]

    def plan_update(self, **kwargs: Any) -> dict[str, Any]:
        return self.coordinator.plan(**kwargs)

    def apply_update(self, plan: dict[str, Any], profile: dict[str, Any], profile_id: str = "default") -> dict[str, Any]:
        return self.coordinator.apply(plan=plan, profile=profile, profile_id=profile_id)

    def compact(self, profile_id: str, *, dry_run: bool = True) -> dict[str, Any]:
        return self.coordinator.compact(profile_id, dry_run=dry_run)

    def rollback(self, profile_id: str, generation: int) -> dict[str, Any]:
        history = self.coordinator.heads.get_head_history(profile_id, limit=50)
        match = next((item for item in history if int(item.get("generation") or 0) == int(generation)), None)
        if match is None:
            return {"status": "error", "reason": "generation_not_found"}
        head = self.coordinator.heads.get_head(profile_id) or {}
        result = self.coordinator.heads.update_head(
            profile_id,
            expected_generation=int(head.get("generation") or 0),
            new_layer_id=str(match.get("layer_id") or ""),
            snapshot_revision=str(head.get("effective_source_revision") or ""),
            append_delta=False,
            reason="rollback",
        )
        return {"status": "ok" if result.success else "error", "error": result.error, "head": self.coordinator.heads.get_head(profile_id)}

    def gc(self, profile_id: str, *, dry_run: bool = True) -> dict[str, Any]:
        return GarbageCollector(self.coordinator.store, self.coordinator.heads).collect(profile_id, dry_run=dry_run).to_dict()


_layer_service = CodeCompassLayerService()


def get_codecompass_layer_service() -> CodeCompassLayerService:
    return _layer_service
