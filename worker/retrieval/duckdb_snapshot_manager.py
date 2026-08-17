"""Immutable snapshot files plus an atomic active-pointer JSON."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from worker.retrieval.duckdb_connection_factory import DuckDBConnectionFactory
from worker.retrieval.duckdb_migration import ensure_current_schema
from worker.retrieval.duckdb_schema import SCHEMA_VERSION, apply_schema
from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig
from worker.retrieval.vector_store_contract import VectorScope, VectorStoreError

try:
    import portalocker
except ImportError:  # pragma: no cover
    portalocker = None


def _pointer_payload(
    *,
    path: Path,
    scope: VectorScope,
    manifest_hash: str,
    compatibility_fingerprint: str,
    source_revision: str,
) -> dict[str, Any]:
    return {
        "schema": "codecompass.duckdb_snapshot_pointer.v1",
        "path": str(path),
        "schema_version": SCHEMA_VERSION,
        "workspace_id": scope.workspace_id,
        "repository_id": scope.repository_id,
        "profile_name": scope.profile_name,
        "domain": scope.domain,
        "manifest_hash": manifest_hash,
        "compatibility_fingerprint": compatibility_fingerprint,
        "source_revision": source_revision,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


class DuckDBSnapshotManager:
    def __init__(self, config: DuckDBVectorStoreConfig, factory: DuckDBConnectionFactory | None = None) -> None:
        self._config = config
        self._factory = factory or DuckDBConnectionFactory(config)

    def pointer_path(self) -> Path:
        return Path(self._config.snapshot_root) / self._config.active_pointer_name

    def snapshot_path(self, scope: VectorScope, fingerprint: str, version: str) -> Path:
        return (
            Path(self._config.snapshot_root)
            / "snapshots"
            / scope.workspace_id
            / scope.repository_id
            / scope.profile_name
            / fingerprint
            / f"{version}.duckdb"
        )

    def read_pointer(self) -> dict[str, Any] | None:
        path = self.pointer_path()
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise VectorStoreError("duckdb_pointer_invalid")
        return payload

    def connect(self, path: str | Path, *, read_only: bool):
        return self._factory.connect(path, read_only=read_only)

    def close_connections(self) -> None:
        self._factory.close_thread()

    def open_active(self, *, read_only: bool = True):
        pointer = self.read_pointer()
        if pointer is None:
            raise VectorStoreError("duckdb_snapshot_missing")
        return self.connect(pointer["path"], read_only=read_only)

    def create_staging(self, scope: VectorScope, fingerprint: str, version: str) -> Path:
        path = self.snapshot_path(scope, fingerprint, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        connection = self._factory.connect(path, read_only=False)
        apply_schema(connection)
        ensure_current_schema(connection)
        return path

    def publish(
        self,
        *,
        staging_path: Path,
        scope: VectorScope,
        manifest_hash: str,
        compatibility_fingerprint: str,
        source_revision: str,
    ) -> dict[str, Any]:
        if not staging_path.exists():
            raise VectorStoreError("duckdb_staging_missing")
        connection = self._factory.connect(staging_path, read_only=False)
        ensure_current_schema(connection)
        connection.execute("DELETE FROM snapshot_meta")
        connection.execute(
            """
            INSERT INTO snapshot_meta
            (schema_version, workspace_id, repository_id, profile_name, domain,
             manifest_hash, compatibility_fingerprint, source_revision, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                SCHEMA_VERSION,
                scope.workspace_id,
                scope.repository_id,
                scope.profile_name,
                scope.domain,
                manifest_hash,
                compatibility_fingerprint,
                source_revision,
                datetime.now(timezone.utc).isoformat(),
            ],
        )
        pointer = _pointer_payload(
            path=staging_path,
            scope=scope,
            manifest_hash=manifest_hash,
            compatibility_fingerprint=compatibility_fingerprint,
            source_revision=source_revision,
        )
        self._write_pointer(pointer)
        self._retain(scope, compatibility_fingerprint)
        return pointer

    def _write_pointer(self, payload: Mapping[str, Any]) -> None:
        target = self.pointer_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(target.parent),
            prefix=".pointer-",
            suffix=".json",
        )
        try:
            if portalocker is not None:
                portalocker.lock(handle, portalocker.LOCK_EX)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, target)

    def _retain(self, scope: VectorScope, fingerprint: str) -> None:
        folder = (
            Path(self._config.snapshot_root)
            / "snapshots"
            / scope.workspace_id
            / scope.repository_id
            / scope.profile_name
            / fingerprint
        )
        if not folder.exists():
            return
        files = sorted(folder.glob("*.duckdb"), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in files[int(self._config.retention_snapshots) :]:
            try:
                stale.unlink()
            except OSError:
                continue
