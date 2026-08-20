from __future__ import annotations

from agent.services.codecompass_hardening_migration_service import (
    CodeCompassHardeningMigrationService,
)


class _Inventory:
    def __init__(self, rows): self.rows = rows
    def inventory(self): return list(self.rows)


class _Journal:
    def __init__(self): self.records = {}
    def load(self, migration_id): return self.records.get(migration_id)
    def save(self, record): self.records[str(record["migration_id"])] = dict(record)


class _Writer:
    def __init__(self): self.migrated = []; self.rolled_back = []
    def migrate(self, operation):
        self.migrated.append(operation["operation_id"])
        return {"target_digest": operation.get("digest", "")}
    def rollback(self, operation, result): self.rolled_back.append(operation["operation_id"])


def _migration(*, enabled=False):
    journal = _Journal()
    writer = _Writer()
    service = CodeCompassHardeningMigrationService(
        inventories={
            "layer_head": _Inventory([{"kind": "layer_head", "legacy_id": "head-v1", "profile_id": "default", "digest": "a" * 64, "secret": "must-not-leak"}]),
            "duckdb_pointer": _Inventory([{"kind": "duckdb_pointer", "legacy_id": "active", "workspace_id": "ws-1", "repository_id": "repo-1", "digest": "b" * 64}]),
            "github_ref": _Inventory([{"kind": "github_ref", "legacy_id": "installation-7", "repository_id": "org/repo", "digest": "c" * 64}]),
        },
        journal=journal,
        writer=writer,
        writes_enabled=enabled,
    )
    return service, journal, writer


def test_dry_run_inventory_is_deterministic_and_secret_free() -> None:
    service, _journal, writer = _migration()
    first = service.run(dry_run=True)
    second = service.run(dry_run=True)
    assert first == second
    assert first["operation_count"] == 3
    assert "must-not-leak" not in str(first)
    assert writer.migrated == []


def test_migration_is_idempotent_resumable_and_rollback_capable() -> None:
    service, _journal, writer = _migration(enabled=True)
    first = service.run(dry_run=False)
    second = service.run(dry_run=False)
    assert first == second
    assert len(writer.migrated) == 3
    rolled_back = service.rollback(first["migration_id"])
    assert rolled_back["status"] == "rolled_back"
    assert len(writer.rolled_back) == 3
