"""Immutable snapshot files plus an atomic active-pointer JSON."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import threading
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


_POINTER_LOCKS_GUARD = threading.Lock()
_POINTER_LOCKS: dict[str, threading.Lock] = {}


def _pointer_lock(scope_key: str) -> threading.Lock:
    with _POINTER_LOCKS_GUARD:
        return _POINTER_LOCKS.setdefault(scope_key, threading.Lock())


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

    @staticmethod
    def _scope_key(scope: VectorScope) -> str:
        raw = json.dumps(
            {
                "workspace_id": scope.workspace_id,
                "repository_id": scope.repository_id,
                "profile_name": scope.profile_name,
                "domain": scope.domain,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def pointer_path(self, scope: VectorScope) -> Path:
        return (
            Path(self._config.snapshot_root)
            / "pointers"
            / self._scope_key(scope)
            / self._config.active_pointer_name
        )

    def snapshot_path(self, scope: VectorScope, fingerprint: str, version: str) -> Path:
        folder = self._snapshot_folder(scope, fingerprint)
        version_key = hashlib.sha256(str(version).encode("utf-8")).hexdigest()
        return folder / f"{version_key}.duckdb"

    def _snapshot_folder(self, scope: VectorScope, fingerprint: str) -> Path:
        return (
            Path(self._config.snapshot_root)
            / "snapshots"
            / self._scope_key(scope)
            / hashlib.sha256(str(fingerprint).encode("utf-8")).hexdigest()
        )

    def read_pointer(self, scope: VectorScope) -> dict[str, Any] | None:
        path = self.pointer_path(scope)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise VectorStoreError("duckdb_pointer_invalid")
        expected = {
            "workspace_id": scope.workspace_id,
            "repository_id": scope.repository_id,
            "profile_name": scope.profile_name,
            "domain": scope.domain,
        }
        if any(str(payload.get(key) or "") != value for key, value in expected.items()):
            raise VectorStoreError("vector_scope_conflict")
        snapshot = Path(str(payload.get("path") or "")).resolve()
        root = Path(self._config.snapshot_root).resolve()
        if root not in snapshot.parents:
            raise VectorStoreError("duckdb_pointer_path_outside_root")
        return payload

    def connect(self, path: str | Path, *, read_only: bool):
        return self._factory.connect(path, read_only=read_only)

    def close_connections(self) -> None:
        self._factory.close_thread()

    def open_active(self, scope: VectorScope, *, read_only: bool = True):
        pointer = self.read_pointer(scope)
        if pointer is None:
            raise VectorStoreError("duckdb_snapshot_missing")
        connection = self.connect(pointer["path"], read_only=read_only)
        rows = connection.execute(
            "SELECT workspace_id, repository_id, profile_name, domain FROM snapshot_meta LIMIT 1"
        ).fetchall()
        if not rows or tuple(str(item) for item in rows[0]) != (
            scope.workspace_id,
            scope.repository_id,
            scope.profile_name,
            scope.domain,
        ):
            raise VectorStoreError("vector_scope_conflict")
        return connection

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
        pointer = self._write_pointer(scope, pointer)
        self._retain(scope, compatibility_fingerprint)
        return pointer

    def _write_pointer(self, scope: VectorScope, payload: Mapping[str, Any]) -> dict[str, Any]:
        with _pointer_lock(self._scope_key(scope)):
            target = self.pointer_path(scope)
            target.parent.mkdir(parents=True, exist_ok=True)
            current = self.read_pointer(scope) or {}
            normalized = dict(payload)
            normalized["generation"] = int(current.get("generation") or 0) + 1
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
                json.dump(normalized, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            os.replace(handle.name, target)
            return normalized

    def migrate_legacy_pointer(self, scope: VectorScope, *, dry_run: bool = True) -> dict[str, Any]:
        """Migrate a global pointer only when the referenced snapshot proves its scope."""

        legacy_path = Path(self._config.snapshot_root) / self._config.active_pointer_name
        if not legacy_path.is_file():
            return {"status": "not_found", "migrated": False}
        try:
            payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise VectorStoreError("duckdb_legacy_pointer_invalid") from exc
        snapshot = Path(str(payload.get("path") or "")).resolve()
        root = Path(self._config.snapshot_root).resolve()
        if root not in snapshot.parents or not snapshot.is_file():
            raise VectorStoreError("duckdb_legacy_pointer_invalid")
        connection = self._factory.connect(snapshot, read_only=True)
        try:
            row = connection.execute(
                "SELECT workspace_id, repository_id, profile_name, domain FROM snapshot_meta LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        expected = (scope.workspace_id, scope.repository_id, scope.profile_name, scope.domain)
        if not row or tuple(str(item) for item in row) != expected:
            raise VectorStoreError("duckdb_legacy_pointer_scope_mismatch")
        if not dry_run:
            self._write_pointer(scope, payload)
            legacy_path.unlink(missing_ok=True)
        return {"status": "planned" if dry_run else "migrated", "migrated": not dry_run}

    def _retain(self, scope: VectorScope, fingerprint: str) -> None:
        folder = self._snapshot_folder(scope, fingerprint)
        if not folder.exists():
            return
        files = sorted(folder.glob("*.duckdb"), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in files[int(self._config.retention_snapshots) :]:
            try:
                stale.unlink()
            except OSError:
                continue
