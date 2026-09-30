"""Dependency-free records for additive legacy source-control migration.

Planner, repository and CLI share these value types; they import no service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.models.source_control_persistence import (
    KnowledgeIndexBindingRecord,
    KnowledgeIndexRunBindingRecord,
)
from ananta_contracts.source_control import (
    SourceConnection,
    SourceRefMapping,
    SourceRevision,
)


class SourceControlMigrationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class LegacySourceSnapshot:
    legacy_source_key: str
    legacy_snapshot_key: str
    tenant_id: str
    project_id: str
    owner_id: str
    connector_type: str
    display_name: str
    sensitivity: str
    enabled: bool
    connection_identity_digest: str
    revision_token: str
    revision_digest: str
    content_manifest_id: str
    content_manifest_digest: str
    admission_state: str
    captured_at_epoch: float


@dataclass(frozen=True)
class LegacyContextPolicyVersion:
    legacy_policy_key: str
    tenant_id: str
    project_id: str
    owner_id: str
    policy_snapshot_id: str
    policy_version: str
    policy_snapshot_digest: str


@dataclass(frozen=True)
class LegacyKnowledgeIndex:
    legacy_index_key: str
    knowledge_index_id: str
    legacy_snapshot_key: str
    legacy_policy_key: str
    status: str
    index_contract_version: str
    artifact_manifest_digest: str | None
    created_at_epoch: float
    updated_at_epoch: float


@dataclass(frozen=True)
class LegacyKnowledgeIndexRun:
    legacy_run_key: str
    index_run_id: str
    legacy_index_key: str
    status: str
    artifact_manifest_digest: str | None
    artifacts_verified: bool
    created_at_epoch: float
    completed_at_epoch: float | None


@dataclass(frozen=True)
class LegacyCitation:
    legacy_citation_key: str
    legacy_snapshot_key: str
    provenance_digest: str


@dataclass(frozen=True)
class LegacyMigrationInventory:
    tenant_id: str
    project_id: str
    owner_id: str
    source_snapshots: tuple[LegacySourceSnapshot, ...] = ()
    context_policies: tuple[LegacyContextPolicyVersion, ...] = ()
    knowledge_indexes: tuple[LegacyKnowledgeIndex, ...] = ()
    index_runs: tuple[LegacyKnowledgeIndexRun, ...] = ()
    citations: tuple[LegacyCitation, ...] = ()

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, Any]
    ) -> "LegacyMigrationInventory":
        scope = dict(payload["scope"])
        return cls(
            tenant_id=str(scope["tenant_id"]),
            project_id=str(scope["project_id"]),
            owner_id=str(scope["owner_id"]),
            source_snapshots=tuple(
                LegacySourceSnapshot(**item)
                for item in payload.get("source_snapshots", ())
            ),
            context_policies=tuple(
                LegacyContextPolicyVersion(**item)
                for item in payload.get("context_policies", ())
            ),
            knowledge_indexes=tuple(
                LegacyKnowledgeIndex(**item)
                for item in payload.get("knowledge_indexes", ())
            ),
            index_runs=tuple(
                LegacyKnowledgeIndexRun(**item)
                for item in payload.get("index_runs", ())
            ),
            citations=tuple(
                LegacyCitation(**item)
                for item in payload.get("citations", ())
            ),
        )



@dataclass(frozen=True)
class MigrationIssue:
    reason_code: str
    legacy_kind: str
    legacy_key: str
    blocking: bool = True


@dataclass(frozen=True)
class MigrationCounts:
    source_snapshots: int = 0
    context_policies: int = 0
    knowledge_indexes: int = 0
    index_runs: int = 0
    citations: int = 0

    @property
    def total(self) -> int:
        return (
            self.source_snapshots
            + self.context_policies
            + self.knowledge_indexes
            + self.index_runs
            + self.citations
        )


@dataclass(frozen=True)
class LegacyMigrationEntry:
    sequence: int
    mapping_id: str
    legacy_kind: str
    legacy_key: str
    legacy_record_digest: str
    tenant_id: str
    project_id: str
    owner_id: str
    connection: SourceConnection | None = None
    revision: SourceRevision | None = None
    source_ref: SourceRefMapping | None = None
    index_binding: KnowledgeIndexBindingRecord | None = None
    run_binding: KnowledgeIndexRunBindingRecord | None = None
    policy_snapshot_id: str | None = None
    policy_version: str | None = None


@dataclass(frozen=True)
class LegacyMigrationPlan:
    migration_id: str
    inventory_digest: str
    tenant_id: str
    project_id: str
    owner_id: str
    counts: MigrationCounts
    entries: tuple[LegacyMigrationEntry, ...]
    issues: tuple[MigrationIssue, ...]

    @property
    def can_apply(self) -> bool:
        return not any(issue.blocking for issue in self.issues)


@dataclass(frozen=True)
class MigrationRunRecord:
    migration_id: str
    tenant_id: str
    project_id: str
    owner_id: str
    inventory_digest: str
    state: str
    cursor: int
    total_entries: int
    created_mapping_count: int
    reused_mapping_count: int
    conflict_count: int
    lock_version: int
    failure_reason: str | None
    started_at_epoch: float
    updated_at_epoch: float
    completed_at_epoch: float | None


@dataclass(frozen=True)
class LegacyMappingRecord:
    mapping_id: str
    migration_id: str
    sequence: int
    legacy_kind: str
    legacy_key: str
    legacy_record_digest: str
    connection_id: str | None
    source_revision_id: str | None
    source_ref_id: str | None
    knowledge_index_id: str | None
    index_run_id: str | None
    policy_snapshot_id: str | None
    policy_version: str | None
    created_source_ref_mapping: bool
    created_index_binding: bool
    created_run_binding: bool


@dataclass(frozen=True)
class MigrationExecutionReport:
    migration_id: str
    dry_run: bool
    state: str
    counts: MigrationCounts
    planned_entries: int
    applied_entries: int
    created_mappings: int
    reused_mappings: int
    issues: tuple[MigrationIssue, ...]
    failure_reason: str | None = None


__all__ = [
    "LegacyCitation",
    "LegacyContextPolicyVersion",
    "LegacyKnowledgeIndex",
    "LegacyKnowledgeIndexRun",
    "LegacyMappingRecord",
    "LegacyMigrationEntry",
    "LegacyMigrationInventory",
    "LegacyMigrationPlan",
    "LegacySourceSnapshot",
    "MigrationCounts",
    "MigrationExecutionReport",
    "MigrationIssue",
    "MigrationRunRecord",
    "SourceControlMigrationError",
]
