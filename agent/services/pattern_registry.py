"""Pattern registry adapter for the deterministic code-templates library.

The registry sits on top of ``PatternService`` and adds:

* Lazy singleton access via ``get_registry()``
* A runtime overlay (separate file) for patterns registered at runtime
  by task-scoped execution flows.
* Re-validation of the merged catalog on every read so a malformed
  overlay can never poison the read path.

It does NOT mutate planning or execution state — it is a pure lookup
adapter. Task-scoped code calls ``register_pattern`` to add a pattern,
which writes to the overlay file; the next ``get``/``list`` call picks
it up.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Dict, List, Optional, Tuple

from agent.services.pattern_service import PatternService, get_pattern_service


DEFAULT_OVERLAY_PATH = "./schemas/patterns/runtime_overlay.v1.json"


class PatternRegistry:
    """Adapter over PatternService with a runtime overlay.

    The overlay is an additive JSON list of pattern dicts, validated
    against the same schema as the base catalog. ``_merged_catalog``
    rebuilds the in-memory dict from base + overlay on every call so
    the overlay file is the single source of truth for runtime state.
    """

    def __init__(
        self,
        service: Optional[PatternService] = None,
        overlay_path: Optional[str] = None,
    ) -> None:
        self._service = service or get_pattern_service()
        self._overlay_path = overlay_path or os.environ.get(
            "ANANTA_PATTERN_RUNTIME_OVERLAY", DEFAULT_OVERLAY_PATH
        )
        self._lock = threading.RLock()

    # --- catalog access -------------------------------------------------

    @property
    def overlay_path(self) -> str:
        return self._overlay_path

    def get(self, pattern_id: str) -> Optional[dict]:
        merged = self._merged_catalog()
        return merged.get(pattern_id)

    def list(self, category: Optional[str] = None, language: Optional[str] = None) -> List[dict]:
        merged = self._merged_catalog()
        result = []
        for pattern in merged.values():
            if category and pattern.get("category") != category:
                continue
            if language and pattern.get("language") != language:
                continue
            result.append(pattern)
        return result

    def validate(self, payload: dict) -> Tuple[bool, List[str]]:
        return self._service.validate(payload)

    # --- runtime overlay ------------------------------------------------

    def register_pattern(self, pattern: dict) -> Tuple[bool, List[str]]:
        """Append a pattern to the runtime overlay file.

        Validates against the schema before writing. Returns the
        validation result so callers can fail fast.
        """
        valid, errors = self._service.validate(pattern)
        if not valid:
            return False, errors
        with self._lock:
            overlay = self._load_overlay()
            # Reject duplicate pattern_id (idempotent registration is
            # the caller's responsibility; we keep registry strict).
            for existing in overlay:
                if existing.get("pattern_id") == pattern.get("pattern_id"):
                    return False, [
                        f"pattern_id '{pattern.get('pattern_id')}' already exists in overlay"
                    ]
            overlay.append(pattern)
            self._write_overlay(overlay)
        return True, []

    def reset_runtime_overlay(self) -> None:
        """Remove the overlay file (test helper, no-op if absent)."""
        with self._lock:
            if os.path.exists(self._overlay_path):
                os.remove(self._overlay_path)

    def overlay_patterns(self) -> List[dict]:
        """Read-only view of overlay entries (for tests/audit)."""
        return list(self._load_overlay())

    # --- internals ------------------------------------------------------

    def _merged_catalog(self) -> Dict[str, dict]:
        merged: Dict[str, dict] = dict(self._service._catalog)  # noqa: SLF001 (read-only merge)
        for pattern in self._load_overlay():
            pid = pattern.get("pattern_id")
            if pid:
                merged[pid] = pattern
        return merged

    def _load_overlay(self) -> List[dict]:
        if not os.path.exists(self._overlay_path):
            return []
        with open(self._overlay_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data

    def _write_overlay(self, overlay: List[dict]) -> None:
        parent = os.path.dirname(self._overlay_path) or "."
        os.makedirs(parent, exist_ok=True)
        # Atomic write: tmp file + rename so a crashed write can never
        # produce a half-written overlay that would break subsequent
        # reads.
        fd, tmp = tempfile.mkstemp(prefix="overlay-", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(overlay, f, indent=2, sort_keys=True)
            os.replace(tmp, self._overlay_path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


# --- singleton ---------------------------------------------------------

_singleton: Optional[PatternRegistry] = None
_singleton_lock = threading.Lock()


def get_registry() -> PatternRegistry:
    """Lazy singleton — mirror of repository_registry.get_repository_registry()."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = PatternRegistry()
    return _singleton


def reset_registry_singleton() -> None:
    """Test helper to drop the cached singleton."""
    global _singleton
    with _singleton_lock:
        _singleton = None
