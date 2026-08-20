"""Dry-run-first, restart-safe migration orchestration for legacy CodeCompass state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

MIGRATION_SCHEMA = "ananta.codecompass_hardening_migration.v1"
_SAFE_FIELDS = frozenset(
    {
        "kind",
        "legacy_id",
        "tenant_id",
        "workspace_id",
        "repository_id",
        "profile_id",
        "domain",
        "digest",
        "target_id",
    }
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


class LegacyCodeCompassInventoryPort(Protocol):
    def inventory(self) -> Sequence[Mapping[str, Any]]: ...


class CodeCompassMigrationJournalPort(Protocol):
    def load(self, migration_id: str) -> Mapping[str, Any] | None: ...
    def save(self, record: Mapping[str, Any]) -> None: ...


class CodeCompassMigrationWriterPort(Protocol):
    def migrate(self, operation: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def rollback(self, operation: Mapping[str, Any], result: Mapping[str, Any]) -> None: ...


class CodeCompassHardeningMigrationService:
    def __init__(
        self,
        *,
        inventories: Mapping[str, LegacyCodeCompassInventoryPort],
        journal: CodeCompassMigrationJournalPort,
        writer: CodeCompassMigrationWriterPort,
        writes_enabled: bool = False,
    ) -> None:
        self._inventories = dict(inventories)
        self._journal = journal
        self._writer = writer
        self._writes_enabled = bool(writes_enabled)

    def plan(self) -> dict[str, Any]:
        operations: list[dict[str, Any]] = []
        for source_kind, inventory in sorted(self._inventories.items()):
            for raw in inventory.inventory():
                sanitized = {
                    key: raw.get(key)
                    for key in sorted(_SAFE_FIELDS)
                    if raw.get(key) not in (None, "")
                }
                sanitized["kind"] = str(sanitized.get("kind") or source_kind)
                sanitized["operation_id"] = _digest({"source_kind": source_kind, "resource": sanitized})
                operations.append(sanitized)
        operations.sort(key=lambda item: item["operation_id"])
        plan_digest = _digest(operations)
        return {
            "schema": MIGRATION_SCHEMA,
            "migration_id": f"cc_hardening_{plan_digest[:24]}",
            "plan_digest": plan_digest,
            "dry_run": True,
            "operation_count": len(operations),
            "operations": operations,
        }

    def run(self, *, dry_run: bool = True) -> dict[str, Any]:
        plan = self.plan()
        if dry_run:
            return plan
        if not self._writes_enabled:
            raise RuntimeError("codecompass_hardening_migration_writes_disabled")
        migration_id = str(plan["migration_id"])
        record = dict(self._journal.load(migration_id) or {})
        if record.get("plan_digest") and record.get("plan_digest") != plan["plan_digest"]:
            raise ValueError("codecompass_hardening_migration_plan_changed")
        completed = dict(record.get("completed") or {})
        record = {
            "schema": MIGRATION_SCHEMA,
            "migration_id": migration_id,
            "plan_digest": plan["plan_digest"],
            "state": "running",
            "completed": completed,
        }
        self._journal.save(record)
        for operation in plan["operations"]:
            operation_id = str(operation["operation_id"])
            if operation_id in completed:
                continue
            record["current_operation_id"] = operation_id
            self._journal.save(record)
            completed[operation_id] = dict(self._writer.migrate(operation))
            record["completed"] = completed
            record.pop("current_operation_id", None)
            self._journal.save(record)
        record["state"] = "completed"
        self._journal.save(record)
        return {
            "schema": MIGRATION_SCHEMA,
            "migration_id": migration_id,
            "status": "completed",
            "migrated": len(completed),
            "plan_digest": plan["plan_digest"],
        }

    def rollback(self, migration_id: str) -> dict[str, Any]:
        if not self._writes_enabled:
            raise RuntimeError("codecompass_hardening_migration_writes_disabled")
        record = dict(self._journal.load(str(migration_id)) or {})
        completed = dict(record.get("completed") or {})
        operations = {item["operation_id"]: item for item in self.plan()["operations"]}
        for operation_id in reversed(sorted(completed)):
            operation = operations.get(operation_id)
            if operation is not None:
                self._writer.rollback(operation, dict(completed[operation_id] or {}))
        record["state"] = "rolled_back"
        self._journal.save(record)
        return {"schema": MIGRATION_SCHEMA, "migration_id": migration_id, "status": "rolled_back"}
