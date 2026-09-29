"""Snapshot manifest identity of the newest completed knowledge index."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

from agent.services.knowledge_index_record_loading import _is_sha256


class KnowledgeIndexManifestIdentitySource(Protocol):
    """What the retrieval service needs to report the current snapshot revision."""

    def current_manifest_identity(self) -> dict[str, Any]: ...


class KnowledgeIndexManifestIdentityResolver:
    """Resolves the current immutable snapshot revision.

    ``completed_indices`` yields the consumable completed indices; the resolver
    never reaches back into the service that composes it.
    """

    def __init__(self, completed_indices: Callable[[], Iterable[Any]]) -> None:
        self._completed_indices = completed_indices

    def current_manifest_identity(self) -> dict[str, Any]:
        """Return the newest immutable snapshot revision, never a host-path hash."""

        indices = sorted(
            list(self._completed_indices()),
            key=lambda item: (
                -float(getattr(item, "updated_at", 0.0) or 0.0),
                str(getattr(item, "id", "") or ""),
            ),
        )
        for index in indices:
            metadata = dict(getattr(index, "index_metadata", None) or {})
            nested = dict(metadata.get("codecompass_snapshot_manifest") or {})
            revision = str(
                metadata.get("codecompass_snapshot_revision")
                or nested.get("snapshot_revision")
                or ""
            ).strip().lower()
            if _is_sha256(revision):
                return {
                    "state": "current",
                    "snapshot_revision": revision,
                    "source_revision": nested.get("source_revision"),
                    "knowledge_index_id": str(getattr(index, "id", "") or ""),
                    "source": "knowledge_index_metadata",
                }
            manifest_path = Path(str(getattr(index, "manifest_path", "") or ""))
            manifest = self._read_manifest_identity(manifest_path)
            if manifest is not None:
                return {
                    **manifest,
                    "knowledge_index_id": str(getattr(index, "id", "") or ""),
                }
        return {
            "state": "degraded",
            "snapshot_revision": None,
            "source_revision": None,
            "knowledge_index_id": None,
            "source": "snapshot_manifest_unavailable",
        }

    @staticmethod
    def _read_manifest_identity(path: Path) -> dict[str, Any] | None:
        if not str(path) or path.is_symlink() or not path.is_file():
            return None
        try:
            if path.stat().st_size > 2_000_000:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        nested = dict(payload.get("codecompass_snapshot_manifest") or payload.get("snapshot_manifest") or {})
        revision = str(
            payload.get("codecompass_snapshot_revision")
            or payload.get("snapshot_revision")
            or nested.get("snapshot_revision")
            or ""
        ).strip().lower()
        if not _is_sha256(revision):
            return None
        return {
            "state": "current",
            "snapshot_revision": revision,
            "source_revision": nested.get("source_revision"),
            "source": "snapshot_manifest",
        }
