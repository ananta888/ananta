from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agent.services.source_control_legacy_migration import (
    MigrationCounts,
    MigrationExecutionReport,
    MigrationIssue,
)
from agent.services.source_control_legacy_migration_cli import (
    SourceControlLegacyMigrationEntrypoint,
    SourceControlMigrationCliError,
    SourceControlMigrationCommand,
)


MIGRATION_ID = "scmig_" + "a" * 64


class _Inventory:
    def load_inventory(self, **scope):
        return SimpleNamespace(**scope)


class _Migration:
    def plan(self, inventory):
        del inventory
        return SimpleNamespace(migration_id=MIGRATION_ID)

    def migrate(self, inventory, *, dry_run, resume=False):
        del inventory, resume
        return MigrationExecutionReport(
            migration_id=MIGRATION_ID,
            dry_run=dry_run,
            state="planned" if dry_run else "applied",
            counts=MigrationCounts(source_snapshots=1),
            planned_entries=1,
            applied_entries=0 if dry_run else 1,
            created_mappings=0 if dry_run else 1,
            reused_mappings=0,
            issues=(
                MigrationIssue(
                    reason_code="legacy_binding_unverified",
                    legacy_kind="source_snapshot",
                    legacy_key="/secret/repository/path",
                    blocking=False,
                ),
            ),
        )

    def rollback(self, migration_id):
        raise AssertionError(migration_id)


class _Runs:
    def get_run(self, migration_id):
        del migration_id
        return None


def _command(action: str, key: str | None = None):
    return SourceControlMigrationCommand(
        action=action,
        tenant_id="tenant-example",
        project_id="project-example",
        owner_id="owner-example",
        idempotency_key=key,
    )


def test_apply_requires_plan_bound_idempotency_key() -> None:
    entrypoint = SourceControlLegacyMigrationEntrypoint(
        inventory=_Inventory(),
        migration=_Migration(),
        runs=_Runs(),
        audit=lambda event: None,
    )
    with pytest.raises(
        SourceControlMigrationCliError,
        match="idempotency_mismatch",
    ):
        entrypoint.execute(_command("apply", "different-key"))


def test_report_aggregates_issues_without_legacy_keys_or_content() -> None:
    audits = []
    entrypoint = SourceControlLegacyMigrationEntrypoint(
        inventory=_Inventory(),
        migration=_Migration(),
        runs=_Runs(),
        audit=audits.append,
    )
    report = entrypoint.execute(_command("dry-run"))

    rendered = str(report)
    assert "/secret/repository/path" not in rendered
    assert report["issues"] == [
        {
            "reason_code": "legacy_binding_unverified",
            "blocking": False,
            "count": 1,
        }
    ]
    assert audits[0].resource_id == MIGRATION_ID
