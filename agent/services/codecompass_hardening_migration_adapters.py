"""Concrete, bounded filesystem ports for CodeCompass hardening migration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.services.codecompass_hardening_migration_service import (
    CodeCompassHardeningMigrationService,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        json.dump(dict(payload), handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


class FilesystemLegacyInventory:
    """Read deterministic JSON inventory records from one bounded directory."""

    def __init__(self, root: str | Path, *, kind: str, pattern: str = "*.json") -> None:
        self._root = Path(root).resolve()
        self._kind = str(kind)
        self._pattern = str(pattern)

    def inventory(self) -> Sequence[Mapping[str, Any]]:
        if not self._root.is_dir():
            return ()
        records: list[dict[str, Any]] = []
        for path in sorted(self._root.glob(self._pattern)):
            resolved = path.resolve()
            if self._root not in resolved.parents or not resolved.is_file():
                continue
            try:
                payload = json.loads(resolved.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            candidates = payload if isinstance(payload, list) else [payload]
            for item in candidates:
                if not isinstance(item, Mapping):
                    continue
                normalized = dict(item)
                normalized.setdefault("kind", self._kind)
                normalized.setdefault("source_name", path.name)
                records.append(normalized)
        return tuple(records)


class JsonFileMigrationJournal:
    """Atomic migration journal suitable for a single Hub deployment volume."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, migration_id: str) -> Path:
        value = str(migration_id)
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("migration_id_invalid")
        return self._root / f"{value}.json"

    def load(self, migration_id: str) -> Mapping[str, Any] | None:
        path = self._path(migration_id)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("migration_journal_invalid")
        return dict(payload)

    def save(self, record: Mapping[str, Any]) -> None:
        migration_id = str(record.get("migration_id") or "")
        _atomic_json(self._path(migration_id), record)


class JsonDirectoryMigrationWriter:
    """Idempotently materialize sanitized migration operations as JSON."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, operation: Mapping[str, Any]) -> Path:
        operation_id = str(operation.get("operation_id") or "")
        if not _SAFE_ID.fullmatch(operation_id):
            raise ValueError("migration_operation_id_invalid")
        return self._root / f"{operation_id}.json"

    def migrate(self, operation: Mapping[str, Any]) -> Mapping[str, Any]:
        path = self._path(operation)
        digest = hashlib.sha256(
            json.dumps(dict(operation), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if path.is_file():
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("operation_digest") != digest:
                raise ValueError("migration_operation_conflict")
            return {"status": "already_migrated", "operation_digest": digest}
        _atomic_json(path, {"operation_digest": digest, "operation": dict(operation)})
        return {"status": "migrated", "operation_digest": digest}

    def rollback(self, operation: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        expected = str(result.get("operation_digest") or "")
        path = self._path(operation)
        if not path.is_file():
            return
        current = json.loads(path.read_text(encoding="utf-8"))
        if str(current.get("operation_digest") or "") != expected:
            raise ValueError("migration_rollback_digest_mismatch")
        path.unlink()


class ObservableMigrationWriter:
    """Small decorator emitting content-free migration lifecycle events."""

    def __init__(
        self,
        delegate,
        observer: Callable[[Mapping[str, Any]], None],
    ) -> None:
        self._delegate = delegate
        self._observer = observer

    def migrate(self, operation: Mapping[str, Any]) -> Mapping[str, Any]:
        operation_id = str(operation.get("operation_id") or "")
        self._observer({"event": "migration_started", "operation_id": operation_id})
        try:
            result = dict(self._delegate.migrate(operation))
        except Exception:
            self._observer({"event": "migration_failed", "operation_id": operation_id})
            raise
        self._observer(
            {
                "event": "migration_completed",
                "operation_id": operation_id,
                "status": str(result.get("status") or ""),
            }
        )
        return result

    def rollback(self, operation: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        self._delegate.rollback(operation, result)
        self._observer(
            {
                "event": "migration_rolled_back",
                "operation_id": str(operation.get("operation_id") or ""),
            }
        )


def build_filesystem_migration_service(
    *,
    inventory_roots: Mapping[str, str | Path],
    journal_root: str | Path,
    output_root: str | Path,
    writes_enabled: bool = False,
    observer: Callable[[Mapping[str, Any]], None] | None = None,
) -> CodeCompassHardeningMigrationService:
    inventories = {
        kind: FilesystemLegacyInventory(root, kind=kind)
        for kind, root in inventory_roots.items()
    }
    writer: Any = JsonDirectoryMigrationWriter(output_root)
    if observer is not None:
        writer = ObservableMigrationWriter(writer, observer)
    return CodeCompassHardeningMigrationService(
        inventories=inventories,
        journal=JsonFileMigrationJournal(journal_root),
        writer=writer,
        writes_enabled=writes_enabled,
    )
