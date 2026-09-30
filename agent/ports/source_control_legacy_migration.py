"""Ports for legacy source inventory and migration persistence."""

from __future__ import annotations

from typing import Protocol

from agent.models.source_control_legacy_migration import (
    LegacyMappingRecord,
    LegacyMigrationEntry,
    LegacyMigrationInventory,
    LegacyMigrationPlan,
    MigrationRunRecord,
)


class LegacySourceInventoryPort(Protocol):
    def load_inventory(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
    ) -> LegacyMigrationInventory: ...



class LegacySourceControlMigrationRepositoryPort(Protocol):
    def begin(
        self,
        plan: LegacyMigrationPlan,
        *,
        resume: bool,
    ) -> MigrationRunRecord: ...

    def apply_entry(
        self,
        *,
        migration_id: str,
        expected_cursor: int,
        entry: LegacyMigrationEntry,
    ) -> MigrationRunRecord: ...

    def finish(
        self,
        *,
        migration_id: str,
        expected_cursor: int,
    ) -> MigrationRunRecord: ...

    def abort(
        self,
        *,
        migration_id: str,
        expected_cursor: int,
        reason_code: str,
    ) -> MigrationRunRecord: ...

    def get_run(self, migration_id: str) -> MigrationRunRecord | None: ...

    def list_mappings(
        self, migration_id: str
    ) -> tuple[LegacyMappingRecord, ...]: ...

    def rollback_new_mappings(
        self, migration_id: str
    ) -> MigrationRunRecord: ...


__all__ = [
    "LegacySourceControlMigrationRepositoryPort",
    "LegacySourceInventoryPort",
]
