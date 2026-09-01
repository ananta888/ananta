"""Immutable tenant-scoped storage for original spreadsheet artifacts."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ananta_contracts.spreadsheet_studio import require_digest, require_id


@dataclass(frozen=True, slots=True)
class StoredSpreadsheetArtifact:
    artifact_id: str
    sha256: str
    size_bytes: int
    format: str
    media_type: str


class SpreadsheetArtifactStore:
    """Content-addressed repository; API consumers only receive opaque IDs."""

    _FORMATS = frozenset({"xlsx", "ods", "csv"})

    def __init__(self, root: str | Path, *, max_bytes: int = 16 * 1024 * 1024) -> None:
        if not 1_024 <= int(max_bytes) <= 256 * 1024 * 1024:
            raise ValueError("spreadsheet_artifact_limit_invalid")
        self._root = Path(root)
        self._max_bytes = int(max_bytes)

    def store(
        self,
        *,
        tenant_id: str,
        content: bytes,
        format: str,
        media_type: str,
        expected_sha256: str,
    ) -> StoredSpreadsheetArtifact:
        tenant = require_id(tenant_id, "tenant_id")
        normalized_format = str(format or "").lower()
        if normalized_format not in self._FORMATS:
            raise ValueError("spreadsheet_artifact_format_invalid")
        if not isinstance(content, bytes) or not 1 <= len(content) <= self._max_bytes:
            raise ValueError("spreadsheet_artifact_size_invalid")
        digest = hashlib.sha256(content).hexdigest()
        if digest != require_digest(expected_sha256, "artifact_digest"):
            raise ValueError("spreadsheet_artifact_digest_mismatch")
        directory = self._root / hashlib.sha256(tenant.encode()).hexdigest()[:32] / digest
        destination = directory / f"original.{normalized_format}"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            if destination.is_symlink() or destination.stat().st_size != len(content):
                raise RuntimeError("spreadsheet_artifact_collision")
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise RuntimeError("spreadsheet_artifact_collision")
        else:
            descriptor, temporary = tempfile.mkstemp(prefix=".upload-", dir=directory)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return StoredSpreadsheetArtifact(
            artifact_id=f"artifact-{digest[:32]}",
            sha256=digest,
            size_bytes=len(content),
            format=normalized_format,
            media_type=str(media_type),
        )

    def read(self, *, tenant_id: str, sha256: str, format: str) -> bytes:
        tenant = require_id(tenant_id, "tenant_id")
        digest = require_digest(sha256, "artifact_digest")
        normalized_format = str(format or "").lower()
        if normalized_format not in self._FORMATS:
            raise ValueError("spreadsheet_artifact_format_invalid")
        path = self._root / hashlib.sha256(tenant.encode()).hexdigest()[:32] / digest / f"original.{normalized_format}"
        if not path.is_file() or path.is_symlink():
            raise KeyError("spreadsheet_artifact_not_found")
        content = path.read_bytes()
        if len(content) > self._max_bytes or hashlib.sha256(content).hexdigest() != digest:
            raise RuntimeError("spreadsheet_artifact_integrity_failed")
        return content


__all__ = ["SpreadsheetArtifactStore", "StoredSpreadsheetArtifact"]
