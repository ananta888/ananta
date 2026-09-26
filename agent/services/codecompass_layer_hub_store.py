"""Hub-owned storage for incremental CodeCompass layers.

Everything the Hub must keep between a commit and a published layer lives
under one root (``<data_dir>/codecompass_layers``): snapshot manifests by
revision, redacted file contents by sha256, and the dispatch records of the
layer jobs. Layers and heads use the existing ``ArtifactLayerStore`` and
``LayerHeadRegistry`` under the same root, so one directory is the whole
state and nothing is shared with a Worker container.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"[0-9a-f]{64}")
_TASK_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _sha256(value: str, what: str) -> str:
    value = str(value or "").strip().lower()
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{what}_invalid")
    return value


class SnapshotManifestStore:
    """Snapshot manifests (``{snapshot_revision, files: [{path, content_sha256, ...}]}``) by revision."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root) / "snapshots"

    def _path(self, revision: str) -> Path:
        revision = _sha256(revision, "snapshot_revision")
        return self._root / revision[:2] / f"{revision}.json.gz"

    def put(self, manifest: Mapping[str, Any]) -> str:
        if not isinstance(manifest, Mapping) or not isinstance(manifest.get("files"), list):
            raise ValueError("snapshot_manifest_invalid")
        revision = _sha256(str(manifest.get("snapshot_revision") or ""), "snapshot_revision")
        target = self._path(revision)
        if not target.exists():
            payload = json.dumps(dict(manifest), sort_keys=True, separators=(",", ":")).encode("utf-8")
            _atomic_write(target, gzip.compress(payload))
        return revision

    def get(self, revision: str) -> dict[str, Any] | None:
        path = self._path(revision)
        if not path.exists():
            return None
        return json.loads(gzip.decompress(path.read_bytes()))


class ContentBlobStore:
    """Redacted file texts addressed by the sha256 of their UTF-8 bytes."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root) / "content"

    def _path(self, digest: str) -> Path:
        digest = _sha256(digest, "content_sha256")
        return self._root / digest[:2] / f"{digest}.gz"

    def has(self, digest: str) -> bool:
        return self._path(digest).exists()

    def missing(self, digests: Iterable[str]) -> list[str]:
        return sorted({str(digest) for digest in digests if not self.has(str(digest))})

    def put(self, digest: str, text: str) -> bool:
        """Store ``text`` under ``digest``; a text that does not hash to it is rejected."""
        data = str(text).encode("utf-8")
        if hashlib.sha256(data).hexdigest() != _sha256(digest, "content_sha256"):
            raise ValueError("content_sha256_mismatch")
        target = self._path(digest)
        if target.exists():
            return False
        _atomic_write(target, gzip.compress(data))
        return True

    def get(self, digest: str) -> str | None:
        path = self._path(digest)
        if not path.exists():
            return None
        return gzip.decompress(path.read_bytes()).decode("utf-8")


class FileLayerDispatchRepository:
    """``CodeCompassLayerDispatchRepositoryPort`` backed by one JSON file per task."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root) / "dispatches"

    def _path(self, task_id: str) -> Path:
        task_id = str(task_id or "")
        if not _TASK_ID.fullmatch(task_id):
            raise ValueError("codecompass_layer_task_id_invalid")
        return self._root / f"{task_id}.json"

    def get(self, task_id: str) -> Mapping[str, Any] | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, record: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(record), sort_keys=True, indent=2).encode("utf-8")
        _atomic_write(self._path(str(record.get("task_id") or "")), payload)
