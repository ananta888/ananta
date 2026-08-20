"""Atomic, generation-tracked pointers to effective layers."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
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
        value = str(profile_id or "default")
        key = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return self.base_path / "heads" / f"{key}.json"

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
            legacy = self.base_path / "heads" / f"{str(profile_id or 'default').replace('/', '_')}.json"
            if not legacy.exists():
                return None
            path = legacy
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_head(self, profile_id: str, payload: dict[str, Any]) -> None:
        target = self._head_path(profile_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".head-", suffix=".json", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)

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
        layer_set: dict[str, str] | None = None,
    ) -> HeadUpdateResult:
        if not self._acquire_lock(profile_id):
            return HeadUpdateResult(False, 0, error="lock_unavailable")
        try:
            if self.get_head(profile_id) is not None:
                return HeadUpdateResult(False, 0, error="head_exists")
            resolved_set = dict(layer_set or {"default": layer_id})
            if not resolved_set or any(not key or not value for key, value in resolved_set.items()):
                return HeadUpdateResult(False, 0, error="layer_set_invalid")
            primary = layer_id or next(iter(resolved_set.values()))
            payload = {
                "schema": "codecompass.layer_head.v1",
                "head_id": profile_id,
                "workspace_id": workspace_id,
                "repository_id": repository_id,
                "profile_id": profile_id,
                "artifact_set_revision": snapshot_revision,
                "base_layer_set": resolved_set,
                "ordered_delta_sets": [],
                "effective_source_revision": snapshot_revision,
                "generation": 1,
                "published_at": datetime.now(timezone.utc).isoformat(),
                "layer_id": primary,
                "history": [{"generation": 1, "layer_id": primary, "layer_set": resolved_set, "reason": reason}],
            }
            self._write_head(profile_id, payload)
            return HeadUpdateResult(True, 1, previous_layer_id=None)
        finally:
            self._release_lock(profile_id)

    def update_head(
        self,
        profile_id: str,
        *,
        expected_generation: int,
        new_layer_id: str,
        snapshot_revision: str | None = None,
        reason: str = "update",
        append_delta: bool = True,
        new_layer_set: dict[str, str] | None = None,
        replace_artifact_kinds: list[str] | None = None,
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
            resolved_set = dict(new_layer_set or {"default": new_layer_id})
            primary = new_layer_id or next(iter(resolved_set.values()), "")
            current["generation"] = generation + 1
            current["layer_id"] = primary
            if snapshot_revision:
                current["effective_source_revision"] = snapshot_revision
                current["artifact_set_revision"] = snapshot_revision
            if append_delta:
                deltas = list(current.get("ordered_delta_sets") or [])
                deltas.append(resolved_set)
                current["ordered_delta_sets"] = deltas
            else:
                replace = set(replace_artifact_kinds or resolved_set)
                base = dict(current.get("base_layer_set") or {})
                base.update(resolved_set)
                current["base_layer_set"] = base
                retained = []
                for delta in list(current.get("ordered_delta_sets") or []):
                    normalized = dict(delta) if isinstance(delta, dict) else {"default": str(delta)}
                    kept = {key: value for key, value in normalized.items() if key not in replace}
                    if kept:
                        retained.append(kept)
                current["ordered_delta_sets"] = retained
            history = list(current.get("history") or [])
            history.append({"generation": current["generation"], "layer_id": primary, "layer_set": resolved_set, "reason": reason})
            current["history"] = history[-50:]
            current["published_at"] = datetime.now(timezone.utc).isoformat()
            self._write_head(profile_id, current)
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
        profiles = []
        for path in (self.base_path / "heads").glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            profile = str(payload.get("profile_id") or "").strip()
            if profile:
                profiles.append(profile)
        return sorted(set(profiles))

    def get_all_heads(self) -> dict[str, Any]:
        return {profile: self.get_head(profile) for profile in self.list_profiles()}
