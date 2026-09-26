"""Garbage collection of the Hub's incremental CodeCompass layer storage.

Mark first, then sweep only what nothing needs and what is older than a
grace period (a layer may be uploaded minutes before its result is
admitted). Kept:

* layers of every head's current chain and of its last ``history_depth``
  generations (rollback window), plus parents of dispatched jobs;
* snapshot manifests of every head, of requested (deferred) commits and of
  dispatched jobs;
* contents referenced by a kept snapshot or a dispatched job's parts;
* dispatch records that are still active, or ended less than
  ``dispatch_retention_seconds`` ago.

Housekeeping of Hub-owned state, not delegated work: Workers cannot reach
this storage, and nothing here builds or changes a layer.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

DEFAULT_GRACE_SECONDS = 60 * 60
DEFAULT_HISTORY_DEPTH = 10
DEFAULT_DISPATCH_RETENTION_SECONDS = 7 * 24 * 60 * 60


class LayerStorageGarbageCollector:
    def __init__(
        self,
        *,
        layers: Any,
        heads: Any,
        snapshots: Any,
        contents: Any,
        dispatches: Any,
        sync_state: Any,
        clock: Any = time.time,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
        history_depth: int = DEFAULT_HISTORY_DEPTH,
        dispatch_retention_seconds: float = DEFAULT_DISPATCH_RETENTION_SECONDS,
    ) -> None:
        self._layers = layers
        self._heads = heads
        self._snapshots = snapshots
        self._contents = contents
        self._dispatches = dispatches
        self._sync_state = sync_state
        self._clock = clock
        self._grace = float(grace_seconds)
        self._history_depth = max(0, int(history_depth))
        self._dispatch_retention = float(dispatch_retention_seconds)

    # --- mark ---------------------------------------------------------------------------

    def _heads_all(self) -> list[Mapping[str, Any]]:
        return [head for head in (self._heads.get_head(p) for p in self._heads.list_profiles()) if head]

    def _reachable_layers(self, heads: list[Mapping[str, Any]], active: list[Mapping[str, Any]]) -> set[str]:
        reachable: set[str] = set()
        for head in heads:
            reachable.update(str(v) for v in dict(head.get("base_layer_set") or {}).values() if v)
            for delta in list(head.get("ordered_delta_sets") or []):
                normalized = dict(delta) if isinstance(delta, Mapping) else {"default": str(delta)}
                reachable.update(str(v) for v in normalized.values() if v)
            history = list(head.get("history") or [])
            for item in history[-self._history_depth:] if self._history_depth else []:
                if item.get("layer_id"):
                    reachable.add(str(item["layer_id"]))
                reachable.update(str(v) for v in dict(item.get("layer_set") or {}).values() if v)
        for record in active:
            parent = ((record.get("intent") or {}).get("plan") or {}).get("parent_layer_id")
            if parent:
                reachable.add(str(parent))
        return reachable

    def _kept_snapshots(self, heads: list[Mapping[str, Any]], active: list[Mapping[str, Any]]) -> set[str]:
        kept = {str(head.get("effective_source_revision") or "") for head in heads}
        kept.update(str(state.get("requested_snapshot") or "") for state in self._sync_state.all())
        for record in active:
            intent = record.get("intent") or {}
            kept.add(str(intent.get("input_revision") or ""))
            kept.add(str((intent.get("plan") or {}).get("from_revision") or ""))
        return {revision for revision in kept if revision}

    def _kept_contents(self, snapshots: set[str], active: list[Mapping[str, Any]]) -> set[str]:
        kept: set[str] = set()
        for revision in snapshots:
            manifest = self._snapshots.get(revision) or {}
            kept.update(str(item.get("content_sha256") or "") for item in list(manifest.get("files") or []))
        for record in active:
            for part in list((record.get("binding") or {}).get("content_parts") or []):
                kept.update(str(digest) for digest in part)
        return {digest for digest in kept if digest}

    # --- sweep -------------------------------------------------------------------------------

    def collect(self, *, dry_run: bool = True) -> dict[str, Any]:
        now = float(self._clock())
        records = list(self._dispatches.iter_records())
        active = [record for record, _mtime in records if record.get("state") in ("planned", "dispatched")]
        heads = self._heads_all()
        layers_kept = self._reachable_layers(heads, active)
        snapshots_kept = self._kept_snapshots(heads, active)
        contents_kept = self._kept_contents(snapshots_kept, active)

        report = {"dry_run": bool(dry_run)}
        report["layers"] = self._sweep(
            self._layers.iter_layer_files(), layers_kept, now, dry_run, self._layers.delete_layer
        )
        report["snapshots"] = self._sweep(
            self._snapshots.iter_entries(), snapshots_kept, now, dry_run, self._snapshots.delete
        )
        report["contents"] = self._sweep(
            self._contents.iter_entries(), contents_kept, now, dry_run, self._contents.delete
        )
        ended = [
            str(record.get("task_id") or "")
            for record, mtime in records
            if record.get("state") in ("published", "failed") and now - mtime >= self._dispatch_retention
        ]
        if not dry_run:
            for task_id in ended:
                self._dispatches.delete(task_id)
        report["dispatches"] = {"kept": len(records) - len(ended), "swept": len(ended)}
        return report

    def _sweep(self, entries, kept: set[str], now: float, dry_run: bool, delete) -> dict[str, int]:
        swept = kept_count = reclaimed = 0
        for key, size, mtime in list(entries):
            if key in kept or now - mtime < self._grace:
                kept_count += 1
                continue
            swept += 1
            reclaimed += int(size)
            if not dry_run:
                delete(key)
        return {"kept": kept_count, "swept": swept, "reclaimed_bytes": reclaimed}


class GarbageCollectionObserver:
    """After a publication, collect at most every ``min_interval`` seconds, off the request path."""

    def __init__(self, collector: Any, *, min_interval: float = 60 * 60, clock: Any = time.time,
                 start: Any = None) -> None:
        self._collector = collector
        self._min_interval = float(min_interval)
        self._clock = clock
        self._last: float | None = None
        self._start = start or _background

    def published(self, publication: Mapping[str, Any]) -> None:
        now = float(self._clock())
        if self._last is not None and now - self._last < self._min_interval:
            return
        self._last = now
        self._start(self._run)

    def _run(self) -> None:
        import logging

        try:
            report = self._collector.collect(dry_run=False)
            logging.info("codecompass layer gc: %s", report)
        except Exception:  # noqa: BLE001 -- housekeeping must never break publishing
            logging.exception("codecompass layer gc failed")


def _background(target: Any) -> None:
    import threading

    threading.Thread(target=target, name="codecompass-layer-gc", daemon=True).start()
