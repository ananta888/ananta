"""Hub-owned planning and reconciliation for additive legacy source adoption."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from agent.services.source_control_persistence import (
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


class LegacySourceInventoryPort(Protocol):
    def load_inventory(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
    ) -> LegacyMigrationInventory: ...


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


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _derived_id(prefix: str, value: object) -> str:
    return f"{prefix}_{_canonical_digest(value)}"


def _record_digest(record: object) -> str:
    return _canonical_digest(asdict(record))


def _is_sha256(value: str | None) -> bool:
    return bool(
        value
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _scope_matches(
    inventory: LegacyMigrationInventory,
    record: object,
) -> bool:
    return (
        getattr(record, "tenant_id", inventory.tenant_id)
        == inventory.tenant_id
        and getattr(record, "project_id", inventory.project_id)
        == inventory.project_id
        and getattr(record, "owner_id", inventory.owner_id)
        == inventory.owner_id
    )


class SourceControlLegacyMigrationPlanner:
    """Pure deterministic planner; it performs no writes or policy decisions."""

    def plan(
        self, inventory: LegacyMigrationInventory
    ) -> LegacyMigrationPlan:
        inventory_digest = _canonical_digest(asdict(inventory))
        migration_id = _derived_id(
            "scmig",
            {
                "inventory_digest": inventory_digest,
                "owner_id": inventory.owner_id,
                "project_id": inventory.project_id,
                "tenant_id": inventory.tenant_id,
            },
        )
        issues: list[MigrationIssue] = []
        entries: list[LegacyMigrationEntry] = []
        snapshots: dict[
            str, tuple[SourceConnection, SourceRevision]
        ] = {}
        policies: dict[str, LegacyContextPolicyVersion] = {}
        indexes: dict[str, KnowledgeIndexBindingRecord] = {}
        seen: set[tuple[str, str]] = set()

        def duplicate(kind: str, key: str) -> bool:
            identity = (kind, key)
            if identity in seen:
                issues.append(
                    MigrationIssue(
                        "legacy_mapping_duplicate",
                        kind,
                        key,
                    )
                )
                return True
            seen.add(identity)
            return False

        def add_entry(
            *,
            kind: str,
            key: str,
            record: object,
            **values: object,
        ) -> None:
            digest = _record_digest(record)
            entries.append(
                LegacyMigrationEntry(
                    sequence=len(entries) + 1,
                    mapping_id=_derived_id(
                        "lmap",
                        {
                            "digest": digest,
                            "kind": kind,
                            "key": key,
                            "migration_id": migration_id,
                        },
                    ),
                    legacy_kind=kind,
                    legacy_key=key,
                    legacy_record_digest=digest,
                    tenant_id=inventory.tenant_id,
                    project_id=inventory.project_id,
                    owner_id=inventory.owner_id,
                    **values,
                )
            )

        for source in sorted(
            inventory.source_snapshots,
            key=lambda item: (
                item.legacy_source_key,
                item.legacy_snapshot_key,
            ),
        ):
            key = source.legacy_snapshot_key
            if duplicate("source_snapshot", key):
                continue
            if not _scope_matches(inventory, source):
                issues.append(
                    MigrationIssue(
                        "legacy_scope_mismatch",
                        "source_snapshot",
                        key,
                    )
                )
                continue
            try:
                connection = SourceConnection.create(
                    tenant_id=source.tenant_id,
                    project_id=source.project_id,
                    owner_id=source.owner_id,
                    connector_type=source.connector_type,
                    connection_identity_digest=(
                        source.connection_identity_digest
                    ),
                    display_name=source.display_name,
                    sensitivity=source.sensitivity,
                    state="active" if source.enabled else "disabled",
                    created_at=datetime.fromtimestamp(
                        source.captured_at_epoch,
                        tz=timezone.utc,
                    ),
                )
                revision = SourceRevision.create(
                    connection_id=connection.connection_id,
                    tenant_id=source.tenant_id,
                    project_id=source.project_id,
                    owner_id=source.owner_id,
                    connector_type=source.connector_type,
                    sensitivity=source.sensitivity,
                    revision_token=source.revision_token,
                    revision_digest=source.revision_digest,
                    content_manifest_id=source.content_manifest_id,
                    content_manifest_digest=(
                        source.content_manifest_digest
                    ),
                    admission_state=source.admission_state,
                    captured_at=datetime.fromtimestamp(
                        source.captured_at_epoch,
                        tz=timezone.utc,
                    ),
                )
            except (TypeError, ValueError):
                issues.append(
                    MigrationIssue(
                        "legacy_source_snapshot_invalid",
                        "source_snapshot",
                        key,
                    )
                )
                continue
            snapshots[key] = (connection, revision)
            add_entry(
                kind="source_snapshot",
                key=key,
                record=source,
                connection=connection,
                revision=revision,
            )

        for policy in sorted(
            inventory.context_policies,
            key=lambda item: item.legacy_policy_key,
        ):
            key = policy.legacy_policy_key
            if duplicate("context_policy", key):
                continue
            if not _scope_matches(inventory, policy):
                issues.append(
                    MigrationIssue(
                        "legacy_scope_mismatch",
                        "context_policy",
                        key,
                    )
                )
                continue
            if not _is_sha256(policy.policy_snapshot_digest):
                issues.append(
                    MigrationIssue(
                        "legacy_policy_digest_invalid",
                        "context_policy",
                        key,
                    )
                )
                continue
            policies[key] = policy
            add_entry(
                kind="context_policy",
                key=key,
                record=policy,
                policy_snapshot_id=policy.policy_snapshot_id,
                policy_version=policy.policy_version,
            )

        for legacy_index in sorted(
            inventory.knowledge_indexes,
            key=lambda item: item.legacy_index_key,
        ):
            key = legacy_index.legacy_index_key
            if duplicate("knowledge_index", key):
                continue
            source = snapshots.get(legacy_index.legacy_snapshot_key)
            policy = policies.get(legacy_index.legacy_policy_key)
            if source is None or policy is None:
                issues.append(
                    MigrationIssue(
                        "legacy_index_binding_unverified",
                        "knowledge_index",
                        key,
                    )
                )
                continue
            if (
                legacy_index.artifact_manifest_digest is not None
                and not _is_sha256(
                    legacy_index.artifact_manifest_digest
                )
            ):
                issues.append(
                    MigrationIssue(
                        "legacy_index_artifact_digest_invalid",
                        "knowledge_index",
                        key,
                    )
                )
                continue
            connection, revision = source
            binding = KnowledgeIndexBindingRecord(
                knowledge_index_id=legacy_index.knowledge_index_id,
                tenant_id=inventory.tenant_id,
                project_id=inventory.project_id,
                owner_id=inventory.owner_id,
                connection_id=connection.connection_id,
                source_revision_id=revision.source_revision_id,
                policy_snapshot_id=policy.policy_snapshot_id,
                policy_snapshot_digest=policy.policy_snapshot_digest,
                index_contract_version=(
                    legacy_index.index_contract_version
                ),
                status=legacy_index.status,
                artifact_manifest_digest=(
                    legacy_index.artifact_manifest_digest
                ),
                activation_requested=False,
                lock_version=1,
                created_at_epoch=legacy_index.created_at_epoch,
                updated_at_epoch=legacy_index.updated_at_epoch,
            )
            indexes[key] = binding
            add_entry(
                kind="knowledge_index",
                key=key,
                record=legacy_index,
                connection=connection,
                revision=revision,
                index_binding=binding,
                policy_snapshot_id=policy.policy_snapshot_id,
                policy_version=policy.policy_version,
            )

        for legacy_run in sorted(
            inventory.index_runs,
            key=lambda item: item.legacy_run_key,
        ):
            key = legacy_run.legacy_run_key
            if duplicate("index_run", key):
                continue
            index = indexes.get(legacy_run.legacy_index_key)
            if index is None:
                issues.append(
                    MigrationIssue(
                        "legacy_run_index_unverified",
                        "index_run",
                        key,
                    )
                )
                continue
            if (
                legacy_run.artifacts_verified
                and not legacy_run.artifact_manifest_digest
            ):
                issues.append(
                    MigrationIssue(
                        "legacy_run_artifact_evidence_missing",
                        "index_run",
                        key,
                    )
                )
                continue
            if (
                legacy_run.artifact_manifest_digest is not None
                and not _is_sha256(
                    legacy_run.artifact_manifest_digest
                )
            ):
                issues.append(
                    MigrationIssue(
                        "legacy_run_artifact_digest_invalid",
                        "index_run",
                        key,
                    )
                )
                continue
            binding = KnowledgeIndexRunBindingRecord(
                index_run_id=legacy_run.index_run_id,
                knowledge_index_id=index.knowledge_index_id,
                tenant_id=index.tenant_id,
                project_id=index.project_id,
                owner_id=index.owner_id,
                source_revision_id=index.source_revision_id,
                policy_snapshot_id=index.policy_snapshot_id,
                policy_snapshot_digest=index.policy_snapshot_digest,
                status=legacy_run.status,
                artifact_manifest_digest=(
                    legacy_run.artifact_manifest_digest
                ),
                artifacts_verified=legacy_run.artifacts_verified,
                lock_version=1,
                created_at_epoch=legacy_run.created_at_epoch,
                completed_at_epoch=legacy_run.completed_at_epoch,
            )
            add_entry(
                kind="index_run",
                key=key,
                record=legacy_run,
                run_binding=binding,
                policy_snapshot_id=index.policy_snapshot_id,
            )

        for citation in sorted(
            inventory.citations,
            key=lambda item: item.legacy_citation_key,
        ):
            key = citation.legacy_citation_key
            if duplicate("citation", key):
                continue
            source = snapshots.get(citation.legacy_snapshot_key)
            if source is None:
                issues.append(
                    MigrationIssue(
                        "legacy_citation_revision_unverified",
                        "citation",
                        key,
                    )
                )
                continue
            connection, revision = source
            try:
                source_ref = SourceRefMapping.create(
                    connection_id=connection.connection_id,
                    source_revision_id=revision.source_revision_id,
                    tenant_id=inventory.tenant_id,
                    project_id=inventory.project_id,
                    provenance_digest=citation.provenance_digest,
                )
            except (TypeError, ValueError):
                issues.append(
                    MigrationIssue(
                        "legacy_citation_invalid",
                        "citation",
                        key,
                    )
                )
                continue
            add_entry(
                kind="citation",
                key=key,
                record=citation,
                connection=connection,
                revision=revision,
                source_ref=source_ref,
            )

        counts = MigrationCounts(
            source_snapshots=len(inventory.source_snapshots),
            context_policies=len(inventory.context_policies),
            knowledge_indexes=len(inventory.knowledge_indexes),
            index_runs=len(inventory.index_runs),
            citations=len(inventory.citations),
        )
        return LegacyMigrationPlan(
            migration_id=migration_id,
            inventory_digest=inventory_digest,
            tenant_id=inventory.tenant_id,
            project_id=inventory.project_id,
            owner_id=inventory.owner_id,
            counts=counts,
            entries=tuple(entries),
            issues=tuple(issues),
        )


class SourceControlLegacyMigrationService:
    """Executes a deterministic plan with per-entry atomic checkpoints."""

    def __init__(
        self,
        *,
        repository: LegacySourceControlMigrationRepositoryPort,
        planner: SourceControlLegacyMigrationPlanner | None = None,
        clock: callable = time.time,
    ) -> None:
        self._repository = repository
        self._planner = planner or SourceControlLegacyMigrationPlanner()
        self._clock = clock

    def plan(
        self, inventory: LegacyMigrationInventory
    ) -> LegacyMigrationPlan:
        return self._planner.plan(inventory)

    def migrate(
        self,
        inventory: LegacyMigrationInventory,
        *,
        dry_run: bool,
        resume: bool = False,
    ) -> MigrationExecutionReport:
        plan = self.plan(inventory)
        if dry_run:
            return MigrationExecutionReport(
                migration_id=plan.migration_id,
                dry_run=True,
                state="blocked" if not plan.can_apply else "planned",
                counts=plan.counts,
                planned_entries=len(plan.entries),
                applied_entries=0,
                created_mappings=0,
                reused_mappings=0,
                issues=plan.issues,
            )
        if not plan.can_apply:
            raise SourceControlMigrationError(
                "source_control_migration_plan_blocked"
            )
        run = self._repository.begin(plan, resume=resume)
        if run.state == "applied":
            return self._report(plan, run)
        for entry in plan.entries[run.cursor :]:
            try:
                run = self._repository.apply_entry(
                    migration_id=plan.migration_id,
                    expected_cursor=run.cursor,
                    entry=entry,
                )
            except Exception as exc:
                reason_code = getattr(
                    exc,
                    "reason_code",
                    "source_control_migration_apply_failed",
                )
                run = self._repository.abort(
                    migration_id=plan.migration_id,
                    expected_cursor=run.cursor,
                    reason_code=str(reason_code),
                )
                return self._report(plan, run)
        run = self._repository.finish(
            migration_id=plan.migration_id,
            expected_cursor=run.cursor,
        )
        return self._report(plan, run)

    def rollback(self, migration_id: str) -> MigrationRunRecord:
        return self._repository.rollback_new_mappings(migration_id)

    @staticmethod
    def _report(
        plan: LegacyMigrationPlan,
        run: MigrationRunRecord,
    ) -> MigrationExecutionReport:
        return MigrationExecutionReport(
            migration_id=plan.migration_id,
            dry_run=False,
            state=run.state,
            counts=plan.counts,
            planned_entries=len(plan.entries),
            applied_entries=run.cursor,
            created_mappings=run.created_mapping_count,
            reused_mappings=run.reused_mapping_count,
            issues=plan.issues,
            failure_reason=run.failure_reason,
        )
