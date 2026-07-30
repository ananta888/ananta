"""Hub-only composition and command contract for deterministic legacy adoption."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from agent.services.source_control_legacy_migration import (
    LegacyMigrationInventory,
    MigrationExecutionReport,
    MigrationRunRecord,
    SourceControlMigrationError,
)
from agent.services.source_control_observability import (
    SourceControlAuditEvent,
    SourceControlAuditOperation,
    SourceControlDecision,
    emit_source_control_audit,
)


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_MIGRATION_ID = re.compile(r"^scmig_[0-9a-f]{64}$")


class SourceControlMigrationCliError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class LegacyInventoryPort(Protocol):
    def load_inventory(
        self, *, tenant_id: str, project_id: str, owner_id: str
    ) -> LegacyMigrationInventory: ...


class LegacyMigrationPort(Protocol):
    def plan(self, inventory: LegacyMigrationInventory) -> object: ...

    def migrate(
        self,
        inventory: LegacyMigrationInventory,
        *,
        dry_run: bool,
        resume: bool = False,
    ) -> MigrationExecutionReport: ...

    def rollback(self, migration_id: str) -> MigrationRunRecord: ...


class LegacyMigrationRunPort(Protocol):
    def get_run(self, migration_id: str) -> MigrationRunRecord | None: ...


@dataclass(frozen=True)
class SourceControlMigrationCommand:
    action: str
    tenant_id: str
    project_id: str
    owner_id: str
    idempotency_key: str | None = None
    migration_id: str | None = None

    def __post_init__(self) -> None:
        if self.action not in {"dry-run", "apply", "resume", "rollback"}:
            raise SourceControlMigrationCliError(
                "source_control_migration_action_invalid"
            )
        for value in (self.tenant_id, self.project_id, self.owner_id):
            if not _ID.fullmatch(str(value or "")):
                raise SourceControlMigrationCliError(
                    "source_control_migration_scope_invalid"
                )


class SourceControlLegacyMigrationEntrypoint:
    """Own migration orchestration in the Hub and emit only bounded reports."""

    def __init__(
        self,
        *,
        inventory: LegacyInventoryPort,
        migration: LegacyMigrationPort,
        runs: LegacyMigrationRunPort,
        audit: Callable[[SourceControlAuditEvent], None] = (
            emit_source_control_audit
        ),
    ) -> None:
        self._inventory = inventory
        self._migration = migration
        self._runs = runs
        self._audit = audit

    def execute(
        self, command: SourceControlMigrationCommand
    ) -> Mapping[str, object]:
        if command.action == "rollback":
            return self._rollback(command)
        inventory = self._inventory.load_inventory(
            tenant_id=command.tenant_id,
            project_id=command.project_id,
            owner_id=command.owner_id,
        )
        plan = self._migration.plan(inventory)
        migration_id = str(getattr(plan, "migration_id", ""))
        if not _MIGRATION_ID.fullmatch(migration_id):
            raise SourceControlMigrationCliError(
                "source_control_migration_plan_identity_invalid"
            )
        if command.action in {"apply", "resume"}:
            if command.idempotency_key != migration_id:
                raise SourceControlMigrationCliError(
                    "source_control_migration_idempotency_mismatch"
                )
        report = self._migration.migrate(
            inventory,
            dry_run=command.action == "dry-run",
            resume=command.action == "resume",
        )
        output = _execution_report(command, report)
        self._emit(
            command=command,
            migration_id=report.migration_id,
            state=report.state,
            operation=(
                SourceControlAuditOperation.validate
                if command.action == "dry-run"
                else SourceControlAuditOperation.lifecycle
            ),
        )
        return output

    def _rollback(
        self, command: SourceControlMigrationCommand
    ) -> Mapping[str, object]:
        migration_id = str(command.migration_id or "")
        if not _MIGRATION_ID.fullmatch(migration_id):
            raise SourceControlMigrationCliError(
                "source_control_migration_id_invalid"
            )
        if command.idempotency_key != f"rollback:{migration_id}":
            raise SourceControlMigrationCliError(
                "source_control_migration_idempotency_mismatch"
            )
        run = self._runs.get_run(migration_id)
        if run is None:
            raise SourceControlMigrationCliError(
                "source_control_migration_not_found"
            )
        if (
            run.tenant_id != command.tenant_id
            or run.project_id != command.project_id
            or run.owner_id != command.owner_id
        ):
            raise SourceControlMigrationCliError(
                "source_control_migration_scope_mismatch"
            )
        rolled_back = self._migration.rollback(migration_id)
        self._emit(
            command=command,
            migration_id=migration_id,
            state=rolled_back.state,
            operation=SourceControlAuditOperation.rollback,
        )
        return {
            "schema": "ananta.source-control.legacy-migration-report.v1",
            "action": "rollback",
            "scope": _scope(command),
            "migration_id": migration_id,
            "state": rolled_back.state,
            "cursor": rolled_back.cursor,
            "total_entries": rolled_back.total_entries,
            "failure_reason": _bounded_reason(rolled_back.failure_reason),
        }

    def _emit(
        self,
        *,
        command: SourceControlMigrationCommand,
        migration_id: str,
        state: str,
        operation: SourceControlAuditOperation,
    ) -> None:
        self._audit(
            SourceControlAuditEvent(
                operation=operation,
                actor_id=command.owner_id,
                tenant_id=command.tenant_id,
                project_id=command.project_id,
                resource_kind="legacy_migration",
                resource_id=migration_id,
                trace_id=(
                    "migration-"
                    + hashlib.sha256(
                        (
                            command.idempotency_key
                            or f"{command.action}:{migration_id}"
                        ).encode("utf-8")
                    ).hexdigest()[:24]
                ),
                decision=(
                    SourceControlDecision.allow
                    if state in {"planned", "applied", "rolled_back"}
                    else SourceControlDecision.deny
                ),
                reason_code=_bounded_reason(state) or "unknown",
            )
        )


def _execution_report(
    command: SourceControlMigrationCommand,
    report: MigrationExecutionReport,
) -> Mapping[str, object]:
    issue_counts = Counter(
        (
            _bounded_reason(issue.reason_code) or "unknown",
            bool(issue.blocking),
        )
        for issue in report.issues
    )
    return {
        "schema": "ananta.source-control.legacy-migration-report.v1",
        "action": command.action,
        "scope": _scope(command),
        "migration_id": report.migration_id,
        "dry_run": report.dry_run,
        "state": report.state,
        "counts": {
            "source_snapshots": report.counts.source_snapshots,
            "context_policies": report.counts.context_policies,
            "knowledge_indexes": report.counts.knowledge_indexes,
            "index_runs": report.counts.index_runs,
            "citations": report.counts.citations,
            "total": report.counts.total,
        },
        "planned_entries": report.planned_entries,
        "applied_entries": report.applied_entries,
        "created_mappings": report.created_mappings,
        "reused_mappings": report.reused_mappings,
        "issues": [
            {
                "reason_code": reason,
                "blocking": blocking,
                "count": count,
            }
            for (reason, blocking), count in sorted(issue_counts.items())
        ],
        "failure_reason": _bounded_reason(report.failure_reason),
    }


def _scope(
    command: SourceControlMigrationCommand,
) -> Mapping[str, str]:
    return {
        "tenant_id": command.tenant_id,
        "project_id": command.project_id,
        "owner_id": command.owner_id,
    }


def _bounded_reason(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower().replace("-", "_")
    if re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,63}", normalized):
        return normalized
    return "reason_other"


def build_hub_source_control_legacy_migration_entrypoint(
) -> SourceControlLegacyMigrationEntrypoint:
    from agent.config import settings
    from agent.database import engine
    from agent.repositories.source_control_migration_repository import (
        SQLSourceControlMigrationRepository,
    )
    from agent.services.source_control_legacy_inventory_adapters import (
        ComposedLegacySourceInventoryAdapter,
        SQLLegacyKnowledgePolicyReader,
        SourceRegistrySnapshotReader,
    )
    from agent.services.source_control_legacy_migration import (
        SourceControlLegacyMigrationService,
    )
    from agent.sources.source_registry import SourceRegistry
    from agent.sources.source_snapshot_store import SourceSnapshotStore

    if settings.role != "hub":
        raise SourceControlMigrationCliError(
            "source_control_migration_hub_role_required"
        )
    repository = SQLSourceControlMigrationRepository(engine)
    service = SourceControlLegacyMigrationService(repository=repository)
    inventory = ComposedLegacySourceInventoryAdapter(
        sources=SourceRegistrySnapshotReader(
            registry=SourceRegistry(),
            snapshots=SourceSnapshotStore(),
        ),
        knowledge=SQLLegacyKnowledgePolicyReader(engine),
    )
    return SourceControlLegacyMigrationEntrypoint(
        inventory=inventory,
        migration=service,
        runs=repository,
    )


def run_source_control_legacy_migration_cli(parsed: Any) -> int:
    try:
        command = SourceControlMigrationCommand(
            action=str(parsed.migration_action),
            tenant_id=str(parsed.tenant_id),
            project_id=str(parsed.project_id),
            owner_id=str(parsed.owner_id),
            idempotency_key=getattr(parsed, "idempotency_key", None),
            migration_id=getattr(parsed, "migration_id", None),
        )
        result = build_hub_source_control_legacy_migration_entrypoint().execute(
            command
        )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result.get("state") not in {"blocked", "aborted"} else 1
    except (SourceControlMigrationCliError, SourceControlMigrationError) as exc:
        print(
            json.dumps(
                {
                    "schema": (
                        "ananta.source-control.legacy-migration-error.v1"
                    ),
                    "status": "failed",
                    "reason_code": _bounded_reason(
                        getattr(exc, "reason_code", None)
                    )
                    or "source_control_migration_failed",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "schema": (
                        "ananta.source-control.legacy-migration-error.v1"
                    ),
                    "status": "failed",
                    "reason_code": (
                        "source_control_migration_runtime_failed"
                    ),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2


__all__ = [
    "SourceControlLegacyMigrationEntrypoint",
    "SourceControlMigrationCliError",
    "SourceControlMigrationCommand",
    "build_hub_source_control_legacy_migration_entrypoint",
    "run_source_control_legacy_migration_cli",
]
