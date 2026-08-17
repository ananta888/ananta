"""Atomic, generation-tracked pointers to effective layers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class HeadUpdateResult:
    """Result of a head update operation."""

    success: bool
    new_generation: int
    previous_layer_id: str | None = None
    error: str | None = None


class LayerHeadRegistry:
    """Atomic registry for layer heads with generation tracking."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path)
        (self.base_path / "heads").mkdir(parents=True, exist_ok=True)

    def _head_path(self, profile_id: str) -> Path:
        safe = str(profile_id or "default").replace("/", "_")
        return self.base_path / "heads" / f"{safe}.json"

    def _lock_path(self, profile_id: str) -> Path:
        return self._head_path(profile_id).with_suffix(".lock")

    def _acquire_lock(self, profile_id: str) -> bool:
        lock = self._lock_path(profile_id)
        try:
            fd = lock.open("x")
            fd.write("1")
            fd.close()
            return True
        except FileExistsError:
            return False

    def _release_lock(self, profile_id: str) -> None:
        lock = self._lock_path(profile_id)
        if lock.exists():
            lock.unlink()

    def get_head(self, profile_id: str) -> dict[str, Any] | None:
        path = self._head_path(profile_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def create_head(
        self,
        profile_id: str,
        *,
        layer_id: str,
        snapshot_revision: str,
        profile_digest: str = "",
        workspace_id: str = "",
        repository_id: str = "",
        reason: str = "create",
    ) -> HeadUpdateResult:
        if self.get_head(profile_id) is not None:
            return HeadUpdateResult(False, 0, error="head_exists")
        payload = {
            "schema": "codecompass.layer_head.v1",
            "head_id": profile_id,
            "workspace_id": workspace_id,
            "repository_id": repository_id,
            "profile_id": profile_id,
            "artifact_set_revision": snapshot_revision,
            "base_layer_set": {"default": layer_id},
            "ordered_delta_sets": [],
            "effective_source_revision": snapshot_revision,
            "generation": 1,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "layer_id": layer_id,
            "history": [{"generation": 1, "layer_id": layer_id, "reason": reason}],
        }
        self._head_path(profile_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return HeadUpdateResult(True, 1, previous_layer_id=None)

    def update_head(
        self,
        profile_id: str,
        *,
        expected_generation: int,
        new_layer_id: str,
        snapshot_revision: str | None = None,
        reason: str = "update",
        append_delta: bool = True,
    ) -> HeadUpdateResult:
        if not self._acquire_lock(profile_id):
            return HeadUpdateResult(False, 0, error="lock_unavailable")
        try:
            current = self.get_head(profile_id)
            if current is None:
                return HeadUpdateResult(False, 0, error="head_missing")
            generation = int(current.get("generation") or 0)
            if generation != int(expected_generation):
                return HeadUpdateResult(False, generation, previous_layer_id=current.get("layer_id"), error="generation_conflict")
            previous = str(current.get("layer_id") or "")
            current["generation"] = generation + 1
            current["layer_id"] = new_layer_id
            if snapshot_revision:
                current["effective_source_revision"] = snapshot_revision
                current["artifact_set_revision"] = snapshot_revision
            if append_delta:
                deltas = list(current.get("ordered_delta_sets") or [])
                deltas.append(new_layer_id)
                current["ordered_delta_sets"] = deltas
            else:
                current["base_layer_set"] = {"default": new_layer_id}
                current["ordered_delta_sets"] = []
            history = list(current.get("history") or [])
            history.append({"generation": current["generation"], "layer_id": new_layer_id, "reason": reason})
            current["history"] = history[-50:]
            current["published_at"] = datetime.now(timezone.utc).isoformat()
            self._head_path(profile_id).write_text(json.dumps(current, indent=2), encoding="utf-8")
            return HeadUpdateResult(True, int(current["generation"]), previous_layer_id=previous)
        finally:
            self._release_lock(profile_id)

    def get_head_history(self, profile_id: str, limit: int = 20) -> list[dict[str, Any]]:
        head = self.get_head(profile_id) or {}
        return list(head.get("history") or [])[-max(1, int(limit)) :]

    def delete_head(self, profile_id: str) -> bool:
        path = self._head_path(profile_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_profiles(self) -> list[str]:
        return sorted(path.stem for path in (self.base_path / "heads").glob("*.json"))

    def get_all_heads(self) -> dict[str, Any]:
        return {profile: self.get_head(profile) for profile in self.list_profiles()}
