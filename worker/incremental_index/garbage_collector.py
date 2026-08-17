"""Mark-and-sweep garbage collection for unreachable layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class GCResult:
    executed_at: str
    profile_name: str
    marked_artifacts: int
    swept_artifacts: int
    reclaimed_bytes: int
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "executed_at": self.executed_at,
            "profile_name": self.profile_name,
            "marked_artifacts": self.marked_artifacts,
            "swept_artifacts": self.swept_artifacts,
            "reclaimed_bytes": self.reclaimed_bytes,
            "errors": self.errors,
        }


class GarbageCollector:
    def __init__(self, layer_store, head_registry) -> None:
        self.layer_store = layer_store
        self.head_registry = head_registry

    def collect(self, profile_name: str, *, dry_run: bool = True) -> GCResult:
        reachable: set[str] = set()
        for profile in ([profile_name] if profile_name else self.head_registry.list_profiles()):
            head = self.head_registry.get_head(profile) or {}
            base = str((head.get("base_layer_set") or {}).get("default") or head.get("layer_id") or "")
            if base:
                reachable.add(base)
            reachable.update(str(item) for item in list(head.get("ordered_delta_sets") or []))
            for item in list(head.get("history") or []):
                if item.get("layer_id"):
                    reachable.add(str(item["layer_id"]))
        swept = 0
        reclaimed = 0
        for meta in self.layer_store.list_layers():
            if meta.layer_id in reachable:
                continue
            swept += 1
            reclaimed += int(meta.size_bytes)
            if not dry_run:
                self.layer_store.delete_layer(meta.layer_id)
        return GCResult(
            executed_at=datetime.now(timezone.utc).isoformat(),
            profile_name=profile_name,
            marked_artifacts=len(reachable),
            swept_artifacts=swept,
            reclaimed_bytes=reclaimed,
        )
