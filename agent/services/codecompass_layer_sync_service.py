"""Hub side of the post-commit CodeCompass layer sync.

A client (the post-commit hook) hands the Hub a snapshot manifest, uploads
only the redacted contents the Hub does not have yet, then asks for the
commit to be indexed. Per profile at most one layer build is in flight:
a commit arriving meanwhile is remembered as the requested snapshot and
the Hub catches up after the running build is published. A missed or
failed commit heals with the next one, because every plan diffs against
the head, not against the previous commit.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.codecompass_layer_hub_store import _atomic_write

SNAPSHOT_SCHEMA = "codecompass.layer_snapshot.v1"
MAX_MANIFEST_FILES = 200_000
MAX_CONTENT_BATCH_CHARS = 32 * 1024 * 1024
STALE_INFLIGHT_SECONDS = 2 * 60 * 60
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled", "canceled", "archived"})
_PROFILE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_COMMIT = re.compile(r"[0-9a-f]{7,64}")


class LayerSyncConflict(ValueError):
    pass


class SyncStateStore:
    """Per profile: the latest requested snapshot and the build in flight."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root) / "sync"

    def _path(self, profile_id: str) -> Path:
        if not _PROFILE.fullmatch(profile_id):
            raise ValueError("codecompass_layer_profile_id_invalid")
        return self._root / f"{profile_id.replace(':', '_')}.json"

    def get(self, profile_id: str) -> dict[str, Any]:
        path = self._path(profile_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def all(self) -> list[dict[str, Any]]:
        if not self._root.exists():
            return []
        states = []
        for path in self._root.glob("*.json"):
            try:
                states.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return states

    def update(self, profile_id: str, **values: Any) -> dict[str, Any]:
        state = {**self.get(profile_id), **values}
        _atomic_write(self._path(profile_id), json.dumps(state, sort_keys=True).encode("utf-8"))
        return state


class CodeCompassLayerSyncService:
    def __init__(self, *, snapshots: Any, contents: Any, layer_service: Any, state: SyncStateStore,
                 clock: Any = time.time, task_status: Any = None) -> None:
        self._snapshots = snapshots
        self._contents = contents
        self._layers = layer_service
        self._state = state
        self._clock = clock
        # Hub task status by id: a build the queue already ended (e.g. failed by the
        # autopilot, never admitted here) must not keep the profile busy.
        self._task_status = task_status

    # --- ingestion ---------------------------------------------------------------------

    def accept_snapshot(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(manifest, Mapping) or manifest.get("schema") != SNAPSHOT_SCHEMA:
            raise ValueError("codecompass_layer_snapshot_schema_invalid")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) > MAX_MANIFEST_FILES:
            raise ValueError("codecompass_layer_snapshot_files_invalid")
        paths = [str(item.get("path") or "") for item in files if isinstance(item, Mapping)]
        if len(paths) != len(files) or len(set(paths)) != len(paths) or any(
            not path or path.startswith("/") or ".." in path.split("/") for path in paths
        ):
            raise ValueError("codecompass_layer_snapshot_paths_invalid")
        revision = self._snapshots.put(manifest)
        digests = [str(item.get("content_sha256") or "") for item in files]
        return {"snapshot_ref": revision, "missing_content_sha256": self._contents.missing(digests)}

    def accept_content(self, texts: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(texts, Mapping) or sum(len(str(text)) for text in texts.values()) > MAX_CONTENT_BATCH_CHARS:
            raise ValueError("codecompass_layer_content_batch_invalid")
        stored = sum(1 for digest, text in texts.items() if self._contents.put(str(digest), str(text)))
        return {"received": len(texts), "stored": stored}

    # --- indexing -----------------------------------------------------------------------------

    def commit(self, *, profile_id: str, snapshot_ref: str, commit_sha: str = "") -> dict[str, Any]:
        if not _PROFILE.fullmatch(str(profile_id or "")):
            raise ValueError("codecompass_layer_profile_id_invalid")
        if commit_sha and not _COMMIT.fullmatch(commit_sha):
            raise ValueError("codecompass_layer_commit_sha_invalid")
        if self._snapshots.get(snapshot_ref) is None:
            raise KeyError("codecompass_snapshot_unknown")
        state = self._state.update(profile_id, requested_snapshot=snapshot_ref, requested_commit=commit_sha,
                                   requested_at=self._clock())
        inflight = state.get("inflight_task") or ""
        if inflight and self._still_running(inflight, float(state.get("inflight_at") or 0)):
            return {"status": "deferred", "inflight_task": inflight, "snapshot_ref": snapshot_ref}
        return self._dispatch(profile_id)

    def _still_running(self, task_id: str, since: float) -> bool:
        if self._clock() - since >= STALE_INFLIGHT_SECONDS:
            return False
        if self._task_status is None:
            return True
        try:
            status = str(self._task_status(task_id) or "").strip().lower()
        except Exception:  # noqa: BLE001 -- unknown status: keep the safe default (busy)
            return True
        return status not in TERMINAL_TASK_STATUSES

    def catch_up(self, profile_id: str) -> dict[str, Any]:
        """After a build finished: clear it, and index the newest requested snapshot if the head lags."""
        state = self._state.update(profile_id, inflight_task="", inflight_at=0)
        head = self._layers.show_head(profile_id) or {}
        requested = str(state.get("requested_snapshot") or "")
        if not requested or requested == str(head.get("effective_source_revision") or ""):
            return {"status": "current"}
        return self._dispatch(profile_id)

    def _dispatch(self, profile_id: str) -> dict[str, Any]:
        state = self._state.get(profile_id)
        snapshot_ref = str(state["requested_snapshot"])
        head = self._layers.show_head(profile_id) or {}
        plan = self._layers.plan_update(profile_id=profile_id, to_snapshot_ref=snapshot_ref)
        missing = self._contents.missing(plan.get("content_sha256") or [])
        if missing:
            raise LayerSyncConflict(f"codecompass_layer_content_missing:{len(missing)}")
        result = dict(self._layers.apply_update(
            plan=plan,
            profile_ref={"profile_id": profile_id},
            profile_id=profile_id,
            expected_generation=int(head.get("generation") or 0),
            idempotency_key=f"{profile_id}:{snapshot_ref}:{int(head.get('generation') or 0)}",
        ))
        if result.get("status") == "queued":
            self._state.update(profile_id, inflight_task=str(result.get("task_id") or ""), inflight_at=self._clock())
        decision = dict(plan.get("decision") or {})
        return {**result, "snapshot_ref": snapshot_ref, "decision": decision.get("decision_type"),
                "file_changes": len(list((plan.get("changeset") or {}).get("file_changes") or []))}


class SyncCatchUpObserver:
    """Publication/failure observer: frees the profile and indexes what arrived meanwhile."""

    def __init__(self, sync: Any) -> None:
        self._sync = sync

    def published(self, publication: Mapping[str, Any]) -> None:
        self._sync().catch_up(str(publication.get("profile_id") or ""))

    def failed(self, profile_id: str) -> None:
        state = self._sync()._state
        state.update(profile_id, inflight_task="", inflight_at=0)
