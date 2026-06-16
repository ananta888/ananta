"""RCHCS-002: JSONL-backed store for ReferencedContextHint records.

Layout under ``store_dir`` (default: ``data/referenced_context_hints/``):
  <kind>.jsonl          — one JSON line per hint of that kind
  manifest.json         — id → {kind, updated_at, staleness_status, path}

Derived hints are stored separately from original code snippets and are
never confused with authoritative source text.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from agent.services.referenced_context_hint_schema import (
    ALL_KINDS,
    STALENESS_INVALID,
    STALENESS_STALE,
    HintValidationError,
    ReferencedContextHint,
    compute_staleness,
    hash_file,
    validate_hint,
)

_DEFAULT_STORE_DIR = "data/referenced_context_hints"
_MANIFEST_FILE = "manifest.json"


class ReferencedContextHintStore:
    """Read/write/search/invalidate hint records.

    Parameters
    ----------
    store_dir:
        Directory for JSONL files + manifest. Created on first write.
    strict:
        If True, reject hints that fail ``validate_hint()``.
        If False, log diagnostics and store anyway (for imports).
    """

    def __init__(self, store_dir: str | Path | None = None, *, strict: bool = True) -> None:
        self._dir = Path(store_dir or _DEFAULT_STORE_DIR)
        self._strict = strict
        self._cache: dict[str, ReferencedContextHint] = {}  # id → hint
        self._loaded_kinds: set[str] = set()

    # ── Public API ────────────────────────────────────────────────────────────

    def put(self, hint: ReferencedContextHint) -> None:
        """Store a hint; raises HintValidationError in strict mode."""
        if self._strict:
            validate_hint(hint)
        hint.updated_at = time.time()
        self._cache[hint.id] = hint
        self._ensure_dir()
        self._append_jsonl(hint)
        self._update_manifest(hint)

    def get(self, hint_id: str) -> ReferencedContextHint | None:
        """Return a hint by ID (loads from disk if not cached)."""
        if hint_id in self._cache:
            return self._cache[hint_id]
        # Brute-force scan (cache miss) — load the relevant kind file
        # Hint ID format: hint:<kind>:<path>:<hash>
        parts = hint_id.split(":")
        if len(parts) >= 2:
            kind = parts[1]
            if kind in ALL_KINDS:
                self._load_kind(kind)
        return self._cache.get(hint_id)

    def search(
        self,
        *,
        path: str | None = None,
        kind: str | None = None,
        domain: str | None = None,
        staleness_exclude: set[str] | None = None,
        limit: int = 20,
    ) -> list[ReferencedContextHint]:
        """Search hints by path prefix, kind, or domain keyword.

        Parameters
        ----------
        staleness_exclude:
            Set of staleness_status values to exclude.
            Defaults to {STALENESS_INVALID}.
        """
        excluded = staleness_exclude if staleness_exclude is not None else {STALENESS_INVALID}

        kinds_to_search = {kind} if kind and kind in ALL_KINDS else ALL_KINDS
        for k in kinds_to_search:
            self._load_kind(k)

        results: list[ReferencedContextHint] = []
        path_lower = (path or "").lower()
        domain_lower = (domain or "").lower()

        for h in self._cache.values():
            if h.staleness_status in excluded:
                continue
            if kind and h.kind != kind:
                continue
            if path:
                if not any(path_lower in r.path.lower() for r in h.source_refs):
                    continue
            if domain_lower:
                combined = (h.title + " " + h.summary).lower()
                if domain_lower not in combined:
                    continue
            results.append(h)
            if len(results) >= limit:
                break

        return sorted(results, key=lambda h: -h.confidence.score)

    def search_by_paths(self, paths: list[str], *, limit: int = 40) -> list[ReferencedContextHint]:
        """Return all fresh/possibly_stale hints whose source_refs match any of the given paths."""
        path_set = {p.lower() for p in paths}
        for k in ALL_KINDS:
            self._load_kind(k)
        results: list[ReferencedContextHint] = []
        for h in self._cache.values():
            if h.staleness_status in (STALENESS_STALE, STALENESS_INVALID):
                continue
            if any(r.path.lower() in path_set for r in h.source_refs):
                results.append(h)
        results.sort(key=lambda h: -h.confidence.score)
        return results[:limit]

    def invalidate(self, hint_id: str, *, reason: str = "manual") -> bool:
        """Mark one hint as invalid in cache + rewrite its JSONL entry."""
        h = self.get(hint_id)
        if h is None:
            return False
        h.staleness_status = STALENESS_INVALID
        h.updated_at = time.time()
        self._rewrite_kind(h.kind)
        self._update_manifest(h)
        return True

    def invalidate_stale_for_paths(
        self,
        changed_paths: list[str],
        *,
        current_manifest_hash: str = "",
    ) -> list[str]:
        """Recompute staleness for all hints touching any of the changed paths.

        Returns list of hint IDs whose staleness changed.
        """
        path_set = set(changed_paths)
        for k in ALL_KINDS:
            self._load_kind(k)

        changed_ids: list[str] = []
        kinds_to_rewrite: set[str] = set()

        for h in list(self._cache.values()):
            primary_paths = {r.path for r in h.source_refs}
            if not primary_paths & path_set:
                continue

            primary = next(iter(primary_paths & path_set), None)
            cur_hash = hash_file(primary) if primary else ""
            exists = bool(cur_hash) or not primary

            new_status = compute_staleness(
                h,
                current_source_hash=cur_hash,
                current_manifest_hash=current_manifest_hash,
                source_exists=exists,
            )
            if new_status != h.staleness_status:
                h.staleness_status = new_status
                h.updated_at = time.time()
                kinds_to_rewrite.add(h.kind)
                changed_ids.append(h.id)

        for k in kinds_to_rewrite:
            self._rewrite_kind(k)
        return changed_ids

    def count(self, *, kind: str | None = None) -> int:
        for k in (ALL_KINDS if not kind else {kind}):
            self._load_kind(k)
        if kind:
            return sum(1 for h in self._cache.values() if h.kind == kind)
        return len(self._cache)

    def stats(self) -> dict[str, Any]:
        for k in ALL_KINDS:
            self._load_kind(k)
        by_kind: dict[str, int] = {}
        by_staleness: dict[str, int] = {}
        for h in self._cache.values():
            by_kind[h.kind] = by_kind.get(h.kind, 0) + 1
            by_staleness[h.staleness_status] = by_staleness.get(h.staleness_status, 0) + 1
        return {
            "total": len(self._cache),
            "by_kind": by_kind,
            "by_staleness": by_staleness,
            "store_dir": str(self._dir),
        }

    # ── Internal I/O ──────────────────────────────────────────────────────────

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _jsonl_path(self, kind: str) -> Path:
        return self._dir / f"{kind}.jsonl"

    def _manifest_path(self) -> Path:
        return self._dir / _MANIFEST_FILE

    def _load_kind(self, kind: str) -> None:
        if kind in self._loaded_kinds:
            return
        self._loaded_kinds.add(kind)
        jpath = self._jsonl_path(kind)
        if not jpath.exists():
            return
        # Replay JSONL: last record for each ID wins (tombstone / update semantics)
        tmp: dict[str, ReferencedContextHint] = {}
        try:
            for line in jpath.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    h = ReferencedContextHint.from_dict(d)
                    tmp[h.id] = h
                except Exception:
                    pass
        except (OSError, PermissionError):
            pass
        self._cache.update(tmp)

    def _append_jsonl(self, hint: ReferencedContextHint) -> None:
        self._ensure_dir()
        jpath = self._jsonl_path(hint.kind)
        line = json.dumps(hint.to_dict(), ensure_ascii=False)
        try:
            with open(jpath, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except (OSError, PermissionError):
            pass

    def _rewrite_kind(self, kind: str) -> None:
        """Compact the JSONL: write only the latest record for each ID."""
        self._ensure_dir()
        hints = {h.id: h for h in self._cache.values() if h.kind == kind}
        jpath = self._jsonl_path(kind)
        try:
            with open(jpath, "w", encoding="utf-8") as fh:
                for h in hints.values():
                    fh.write(json.dumps(h.to_dict(), ensure_ascii=False) + "\n")
        except (OSError, PermissionError):
            pass

    def _update_manifest(self, hint: ReferencedContextHint) -> None:
        manifest = self._read_manifest()
        primary_path = hint.source_refs[0].path if hint.source_refs else ""
        manifest[hint.id] = {
            "kind": hint.kind,
            "updated_at": hint.updated_at,
            "staleness_status": hint.staleness_status,
            "path": primary_path,
        }
        self._write_manifest(manifest)

    def _read_manifest(self) -> dict[str, Any]:
        mpath = self._manifest_path()
        if not mpath.exists():
            return {}
        try:
            return json.loads(mpath.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        self._ensure_dir()
        try:
            self._manifest_path().write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2)
            )
        except (OSError, PermissionError):
            pass


# ── Module singleton ──────────────────────────────────────────────────────────

_instance: ReferencedContextHintStore | None = None


def get_referenced_context_hint_store(
    store_dir: str | Path | None = None,
) -> ReferencedContextHintStore:
    global _instance
    if _instance is None:
        _instance = ReferencedContextHintStore(store_dir)
    return _instance


def reset_referenced_context_hint_store(new: ReferencedContextHintStore | None = None) -> None:
    global _instance
    _instance = new
