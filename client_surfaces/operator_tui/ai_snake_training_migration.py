from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class MigrationResult:
    status: str  # ok | degraded
    schema_version: str
    readonly: bool
    reason: str = ""


MigrationFn = Callable[[dict[str, Any]], dict[str, Any]]


class MigrationRegistry:
    def __init__(self) -> None:
        self._registry: dict[tuple[str, str], MigrationFn] = {}

    def register(self, from_version: str, to_version: str, fn: MigrationFn) -> None:
        self._registry[(str(from_version), str(to_version))] = fn

    def migrate(self, payload: dict[str, Any], *, to_version: str) -> tuple[dict[str, Any], MigrationResult]:
        source_version = str(payload.get("schema_version") or "")
        target = str(to_version)
        if source_version == target:
            return dict(payload), MigrationResult(status="ok", schema_version=target, readonly=False)
        key = (source_version, target)
        fn = self._registry.get(key)
        if fn is None:
            return dict(payload), MigrationResult(
                status="degraded",
                schema_version=source_version or "unknown",
                readonly=True,
                reason="unknown_future_version",
            )
        migrated = fn(dict(payload))
        migrated["schema_version"] = target
        return migrated, MigrationResult(status="ok", schema_version=target, readonly=False)


def backup_before_write(path: Path) -> Path | None:
    target = Path(path)
    if not target.exists():
        return None
    backup = target.with_suffix(target.suffix + ".bak")
    shutil.copy2(target, backup)
    return backup


def save_with_migration(
    *,
    path: Path,
    payload: dict[str, Any],
    target_version: str,
    registry: MigrationRegistry,
) -> MigrationResult:
    migrated, result = registry.migrate(payload, to_version=target_version)
    if result.readonly:
        return result
    backup_before_write(path)
    Path(path).write_text(json.dumps(migrated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result
