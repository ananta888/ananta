"""Concrete Hub adapters for governed source-revision index composition."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import time
from typing import Any

from sqlmodel import Session, select

from agent.db_models.knowledge_index_execution import (
    KnowledgeIndexExecutionBindingDB,
)
from agent.db_models.source_access_enforcement import (
    SourceAccessGrantExecutionPolicyDB,
)
from agent.db_models.source_admission_receipt import SourceAdmissionReceiptDB
from agent.db_models.source_control import (
    SourceAccessGrantDB,
    SourceConnectionSelectorDB,
    SourceRevisionDB,
)
from agent.repositories.knowledge_index_execution_repository import (
    SQLKnowledgeIndexExecutionRepository,
)
from agent.repositories.source_control_repository import (
    SQLSourceControlRepository,
)
from agent.services.knowledge_index_execution_binding_service import (
    CurrentKnowledgeIndexAuthority,
    KnowledgeIndexExecutionBindingService,
)
from agent.services.knowledge_index_worker_artifact_service import (
    KnowledgeIndexWorkerArtifactService,
)
from agent.services.knowledge_index_source_control_projection import (
    KnowledgeIndexSourceControlCompletionProjector,
)
from agent.services.source_access_enforcement import (
    source_access_grant_digest,
)
from agent.services.source_access_manifest_signing import SourceAccessSigningKey
from agent.services.source_access_persistence_adapter import (
    SQLSourceAccessEnforcementAdapter,
)
from agent.services.source_admission_service import SourceAdmissionBudgets
from agent.services.source_control_index_authority_planner import (
    BoundSourceIndexAuthority,
    BoundSourceRevisionAuthority,
    BoundSourceRevisionAuthorityPlanner,
    BoundSourceRevisionPayload,
    BoundSourceRevisionPlanningError,
)
from agent.services.source_destination_resolution import (
    DestinationSelection,
    SourceDestinationResolutionService,
)
from agent.services.strict_source_control_knowledge_index_composition import (
    StrictGovernedKnowledgeIndexDependencies,
    build_strict_governed_knowledge_index_job_service,
)
from agent.services.source_filesystem_scanner import (
    ProductionFilesystemSourceScanner,
)
from ananta_contracts.knowledge_index_execution import (
    KnowledgeIndexExecutionAssignment,
    KnowledgeIndexExecutionJob,
    KnowledgeIndexResourceBudget,
)
from ananta_contracts.source_control import SourceAccessGrant


_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_DIRECTORY_FLAGS = _READ_FLAGS | getattr(os, "O_DIRECTORY", 0)


def _grant_contract(row: SourceAccessGrantDB) -> SourceAccessGrant:
    contract = SourceAccessGrant.create(
        version=row.grant_version,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        source_revision_id=row.source_revision_id,
        destination_id=row.destination_id,
        operation=row.operation,
        transformation=row.transformation,
        purpose=row.purpose,
        policy_version=row.policy_version,
        policy_snapshot_digest=row.policy_snapshot_digest,
        state=row.state,
        issued_at=datetime.fromtimestamp(row.issued_at_epoch, tz=timezone.utc),
        expires_at=datetime.fromtimestamp(row.expires_at_epoch, tz=timezone.utc),
    )
    if contract.grant_id != row.grant_id:
        raise BoundSourceRevisionPlanningError("source_access_grant_digest_mismatch")
    return contract


def _admission_receipt(
    session: Session,
    revision: SourceRevisionDB,
) -> SourceAdmissionReceiptDB | None:
    return session.exec(
        select(SourceAdmissionReceiptDB)
        .where(
            SourceAdmissionReceiptDB.tenant_id == revision.tenant_id,
            SourceAdmissionReceiptDB.project_id == revision.project_id,
            SourceAdmissionReceiptDB.source_revision_id
            == revision.source_revision_id,
            SourceAdmissionReceiptDB.decision_state == "admitted",
            SourceAdmissionReceiptDB.revision_digest
            == revision.revision_digest,
            SourceAdmissionReceiptDB.manifest_digest
            == revision.content_manifest_digest,
        )
        .order_by(SourceAdmissionReceiptDB.evaluated_at_epoch.desc())
    ).first()


class SQLBoundSourceRevisionAuthorityAdapter:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def resolve_bound_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        source_revision_id: str,
    ) -> BoundSourceRevisionAuthority | None:
        with Session(self._engine) as session:
            revision = session.exec(
                select(SourceRevisionDB).where(
                    SourceRevisionDB.source_revision_id == source_revision_id,
                    SourceRevisionDB.connection_id == connection_id,
                    SourceRevisionDB.tenant_id == tenant_id,
                    SourceRevisionDB.project_id == project_id,
                )
            ).first()
            selector = session.get(SourceConnectionSelectorDB, connection_id)
            if revision is None or selector is None:
                return None
            receipt = _admission_receipt(session, revision)
            if receipt is None:
                return None
            return BoundSourceRevisionAuthority(
                tenant_id=revision.tenant_id,
                project_id=revision.project_id,
                connection_id=revision.connection_id,
                source_revision_id=revision.source_revision_id,
                source_revision_digest=revision.revision_digest,
                content_manifest_digest=revision.content_manifest_digest,
                connector_type=revision.connector_type,
            source_id=f"source-control:{revision.connection_id}",
                admission_state=receipt.decision_state,
                admission_digest=receipt.admission_digest,
            )


class RegisteredWorkspaceBoundSourcePayloadAdapter:
    """Reopen one exact admitted workspace manifest without following links."""

    def __init__(
        self,
        *,
        engine: Any,
        workspace_catalog: Any,
        workspace_connector: Any,
        scanner: ProductionFilesystemSourceScanner,
        budgets: SourceAdmissionBudgets,
        remote_payloads: Any | None = None,
    ) -> None:
        self._engine = engine
        self._workspaces = workspace_catalog
        self._connector = workspace_connector
        self._scanner = scanner
        self._budgets = budgets
        self._remote_payloads = remote_payloads

    def load_bound_revision_payload(
        self,
        revision: BoundSourceRevisionAuthority,
    ) -> BoundSourceRevisionPayload:
        if revision.connector_type in {
            "git",
            "github",
            "generic_git",
            "github_repository",
        }:
            if self._remote_payloads is None:
                raise BoundSourceRevisionPlanningError(
                    "source_revision_payload_connector_unsupported"
                )
            return self._remote_payloads.load_bound_revision_payload(revision)
        if revision.connector_type not in {
            "registered_workspace",
            "local_directory",
        }:
            raise BoundSourceRevisionPlanningError(
                "source_revision_payload_connector_unsupported"
            )
        with Session(self._engine) as session:
            row = session.get(SourceRevisionDB, revision.source_revision_id)
            selector = session.get(
                SourceConnectionSelectorDB, revision.connection_id
            )
        if row is None or selector is None:
            raise BoundSourceRevisionPlanningError(
                "source_revision_payload_binding_missing"
            )
        workspace = self._workspaces.get(
            workspace_id=selector.selector_id,
            tenant_id=revision.tenant_id,
            project_id=revision.project_id,
            owner_id=row.owner_id,
        )
        if workspace is None:
            raise BoundSourceRevisionPlanningError(
                "source_revision_workspace_unavailable"
            )
        snapshot = self._connector.inventory(
            tenant_id=revision.tenant_id,
            project_id=revision.project_id,
            workspace_id=selector.selector_id,
            relative_path=selector.relative_path or ".",
        )
        if (
            snapshot.revision_digest != revision.source_revision_digest
            or snapshot.manifest_digest != revision.content_manifest_digest
        ):
            raise BoundSourceRevisionPlanningError("source_revision_stale")
        scan = self._scanner.scan(
            workspace=workspace,
            snapshot=snapshot,
            budgets=self._budgets,
        )
        if (
            not scan.scan.completed
            or scan.scan.scan_error_count
            or scan.inventory.revision_digest != revision.source_revision_digest
            or scan.inventory.manifest_digest
            != revision.content_manifest_digest
        ):
            raise BoundSourceRevisionPlanningError(
                "source_revision_payload_scan_failed"
            )

        files: list[dict[str, object]] = []
        records: list[dict[str, object]] = []
        for entry in snapshot.entries:
            content = self._read_exact_file(
                workspace.root,
                snapshot.relative_root,
                entry.relative_path,
                expected_size=entry.byte_size,
                expected_digest=entry.content_digest,
            )
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BoundSourceRevisionPlanningError(
                    "source_revision_payload_text_required"
                ) from exc
            files.append(
                {
                    "relative_path": entry.relative_path,
                    "sha256": entry.content_digest,
                    "size_bytes": entry.byte_size,
                }
            )
            records.append(
                {
                    "id": entry.relative_path,
                    "content": text,
                    "metadata": {
                        "relative_path": entry.relative_path,
                        "file_type": entry.file_type,
                    },
                }
            )
        return BoundSourceRevisionPayload(
            source_revision_id=revision.source_revision_id,
            source_revision_digest=revision.source_revision_digest,
            content_manifest_digest=revision.content_manifest_digest,
            files=tuple(files),
            records=tuple(records),
        )

    @staticmethod
    def _read_exact_file(
        workspace_root: Path,
        relative_root: str,
        relative_path: str,
        *,
        expected_size: int,
        expected_digest: str,
    ) -> bytes:
        parts = [
            part
            for part in (
                *PurePosixPath(relative_root).parts,
                *PurePosixPath(relative_path).parts,
            )
            if part not in {"", "."}
        ]
        if not parts or any(part == ".." or "/" in part for part in parts):
            raise BoundSourceRevisionPlanningError(
                "source_revision_payload_path_invalid"
            )
        descriptors: list[int] = []
        try:
            root_fd = os.open(workspace_root, _DIRECTORY_FLAGS)
            descriptors.append(root_fd)
            root_stat = os.fstat(root_fd)
            current_fd = root_fd
            for part in parts[:-1]:
                current_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
                descriptors.append(current_fd)
                opened = os.fstat(current_fd)
                if opened.st_dev != root_stat.st_dev:
                    raise BoundSourceRevisionPlanningError(
                        "source_revision_payload_mount_changed"
                    )
            file_fd = os.open(parts[-1], _READ_FLAGS, dir_fd=current_fd)
            descriptors.append(file_fd)
            before = os.fstat(file_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_dev != root_stat.st_dev
                or before.st_nlink != 1
                or before.st_size != expected_size
            ):
                raise BoundSourceRevisionPlanningError(
                    "source_revision_payload_file_invalid"
                )
            chunks: list[bytes] = []
            remaining = expected_size + 1
            while remaining > 0:
                chunk = os.read(file_fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(file_fd)
            if (
                len(content) != expected_size
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or hashlib.sha256(content).hexdigest() != expected_digest
            ):
                raise BoundSourceRevisionPlanningError(
                    "source_revision_payload_file_changed"
                )
            return content
        except OSError as exc:
            raise BoundSourceRevisionPlanningError(
                "source_revision_payload_read_failed"
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


class SQLBoundSourceIndexAuthorityAdapter:
    """Select one unambiguous persisted grant/destination and issue a lease."""

    def __init__(self, *, engine: Any, destinations: Any, clock=time.time) -> None:
        self._engine = engine
        self._destinations = destinations
        self._resolver = SourceDestinationResolutionService(destinations)
        self._clock = clock

    def resolve_bound_index_authority(
        self,
        *,
        revision: BoundSourceRevisionAuthority,
        actor_id: str,
        idempotency_key: str,
    ) -> BoundSourceIndexAuthority:
        del actor_id
        now = float(self._clock())
        key_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        with Session(self._engine) as session:
            grants = session.exec(
                select(SourceAccessGrantDB).where(
                    SourceAccessGrantDB.tenant_id == revision.tenant_id,
                    SourceAccessGrantDB.project_id == revision.project_id,
                    SourceAccessGrantDB.source_revision_id
                    == revision.source_revision_id,
                    SourceAccessGrantDB.operation == "index",
                    SourceAccessGrantDB.transformation == "redacted",
                    SourceAccessGrantDB.purpose == "knowledge-index",
                    SourceAccessGrantDB.state == "active",
                    SourceAccessGrantDB.issued_at_epoch <= now,
                    SourceAccessGrantDB.expires_at_epoch > now,
                )
            ).all()
            candidates = []
            for grant_row in grants:
                policy = session.get(
                    SourceAccessGrantExecutionPolicyDB, grant_row.grant_id
                )
                descriptor = self._destinations.get(
                    tenant_id=revision.tenant_id,
                    project_id=revision.project_id,
                    destination_id=grant_row.destination_id,
                )
                if policy is None or descriptor is None:
                    continue
                selection = DestinationSelection(
                    worker_id=descriptor.worker_id,
                    runtime_id=descriptor.runtime_id,
                    provider_id=descriptor.provider_id,
                    model_id=descriptor.model_id,
                )
                resolved = self._resolver.resolve(selection)
                if (
                    resolved.descriptor.destination_id
                    != grant_row.destination_id
                    or resolved.destination_digest != policy.destination_digest
                    or not grant_row.policy_snapshot_digest
                ):
                    continue
                candidates.append((grant_row, policy, selection, resolved))
            if len(candidates) != 1:
                raise BoundSourceRevisionPlanningError(
                    "source_index_authority_unambiguous_grant_required"
                )
            grant_row, _policy, selection, resolved = candidates[0]
            existing = session.exec(
                select(KnowledgeIndexExecutionBindingDB).where(
                    KnowledgeIndexExecutionBindingDB.tenant_id
                    == revision.tenant_id,
                    KnowledgeIndexExecutionBindingDB.project_id
                    == revision.project_id,
                    KnowledgeIndexExecutionBindingDB.idempotency_key_digest
                    == key_digest,
                )
            ).first()
            if existing is not None:
                if (
                    existing.source_revision_id != revision.source_revision_id
                    or existing.source_revision_digest
                    != revision.source_revision_digest
                    or existing.admission_digest != revision.admission_digest
                    or existing.destination_id
                    != resolved.descriptor.destination_id
                    or existing.source_access_grant_id != grant_row.grant_id
                ):
                    raise BoundSourceRevisionPlanningError(
                        "source_index_idempotency_authority_conflict"
                    )
                assignment = KnowledgeIndexExecutionJob.model_validate(
                    dict(existing.envelope_json)
                ).assignment
            else:
                now_ms = int(now * 1000)
                expires_ms = min(
                    int(grant_row.expires_at_epoch * 1000),
                    now_ms + 300_000,
                )
                if expires_ms <= now_ms:
                    raise BoundSourceRevisionPlanningError(
                        "source_index_assignment_lease_inactive"
                    )
                coordinate = hashlib.sha256(
                    (
                        revision.source_revision_id
                        + resolved.descriptor.destination_id
                        + idempotency_key
                    ).encode("utf-8")
                ).hexdigest()
                assignment = KnowledgeIndexExecutionAssignment(
                    assignment_id=f"assignment_{coordinate}",
                    worker_id=selection.worker_id,
                    lease_id=f"lease_{coordinate}",
                    lease_generation=1,
                    lease_issued_epoch_ms=now_ms,
                    lease_expires_epoch_ms=expires_ms,
                )
        grant = _grant_contract(grant_row)
        return BoundSourceIndexAuthority(
            policy_snapshot_id=grant.policy_version,
            policy_snapshot_digest=str(grant.policy_snapshot_digest),
            destination_selection=selection,
            source_access_grant_id=grant.grant_id,
            source_access_grant_digest=source_access_grant_digest(grant),
            resources=KnowledgeIndexResourceBudget(
                max_files=10_000,
                max_total_bytes=512 * 1024 * 1024,
                max_file_bytes=16 * 1024 * 1024,
                max_runtime_seconds=900,
                max_memory_bytes=2 * 1024 * 1024 * 1024,
                max_output_bytes=384 * 1024 * 1024,
            ),
            assignment=assignment,
        )


class SQLCurrentKnowledgeIndexAuthorityAdapter:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        destination_id: str,
        source_access_grant_id: str,
    ) -> CurrentKnowledgeIndexAuthority | None:
        with Session(self._engine) as session:
            revision = session.get(SourceRevisionDB, source_revision_id)
            grant_row = session.get(SourceAccessGrantDB, source_access_grant_id)
            policy = session.get(
                SourceAccessGrantExecutionPolicyDB, source_access_grant_id
            )
            if (
                revision is None
                or grant_row is None
                or policy is None
                or revision.tenant_id != tenant_id
                or revision.project_id != project_id
                or revision.admission_state != "admitted"
                or grant_row.tenant_id != tenant_id
                or grant_row.project_id != project_id
                or grant_row.source_revision_id != source_revision_id
                or grant_row.destination_id != destination_id
                or grant_row.state != "active"
                or grant_row.expires_at_epoch <= time.time()
                or not grant_row.policy_snapshot_digest
            ):
                return None
            receipt = _admission_receipt(session, revision)
            if receipt is None:
                return None
            grant = _grant_contract(grant_row)
            return CurrentKnowledgeIndexAuthority(
                tenant_id=tenant_id,
                project_id=project_id,
                source_revision_id=source_revision_id,
                source_revision_digest=revision.revision_digest,
                admission_digest=receipt.admission_digest,
                policy_snapshot_id=grant.policy_version,
                policy_snapshot_digest=str(grant.policy_snapshot_digest),
                destination_id=destination_id,
                destination_digest=policy.destination_digest,
                source_access_grant_id=grant.grant_id,
                source_access_grant_digest=source_access_grant_digest(grant),
            )


class IngestionKnowledgeIndexPayloadStore:
    """Persist payload bytes through the existing SQL-backed artifact service."""

    def __init__(self, *, ingestion: Any, artifact_repository: Any) -> None:
        self._ingestion = ingestion
        self._artifacts = artifact_repository

    def store_payload(
        self,
        *,
        content: bytes,
        fingerprint: str,
        created_by: str | None,
    ) -> dict[str, object]:
        if hashlib.sha256(content).hexdigest() != fingerprint:
            raise ValueError("knowledge_index_payload_fingerprint_mismatch")
        artifact, version, _collection = self._ingestion.upload_artifact(
            filename=f"knowledge-index-payload-{fingerprint}.json",
            content=content,
            created_by=created_by or "knowledge-index-api",
            media_type="application/vnd.ananta.knowledge-index-job+json",
        )
        artifact.artifact_metadata = {
            **dict(artifact.artifact_metadata or {}),
            "system_artifact_kind": "knowledge_index_job_payload",
            "idempotency_fingerprint": fingerprint,
        }
        self._artifacts.save(artifact)
        return {
            "artifact_id": artifact.id,
            "sha256": version.sha256,
            "size_bytes": version.size_bytes,
            "media_type": version.media_type,
        }


@dataclass(frozen=True)
class SourceControlIndexProductionComposition:
    planner: BoundSourceRevisionAuthorityPlanner
    job_service: Any
    execution_binding_service: KnowledgeIndexExecutionBindingService
    payload_store: IngestionKnowledgeIndexPayloadStore
    worker_artifact_service: KnowledgeIndexWorkerArtifactService


def build_source_control_index_production_composition(
    *,
    app: Any,
    engine: Any,
    destination_catalog: Any,
    workspace_catalog: Any,
    workspace_connector: Any,
    scanner: ProductionFilesystemSourceScanner,
    budgets: SourceAdmissionBudgets,
    signing_key: SourceAccessSigningKey,
) -> SourceControlIndexProductionComposition:
    from agent.services.repository_registry import get_repository_registry
    from agent.services.service_registry import get_core_services

    repositories = get_repository_registry(app)
    core = get_core_services(app)
    remote_payload_adapter = None
    remote_payload_store = app.extensions.get("remote_source_payload_store")
    remote_registry = app.extensions.get(
        "source_control_registered_remote_catalog"
    )
    if remote_payload_store is not None and remote_registry is not None:
        from agent.services.remote_git_bound_payload import (
            RemoteGitBoundSourcePayloadAdapter,
        )

        remote_payload_adapter = RemoteGitBoundSourcePayloadAdapter(
            engine=engine,
            payload_store=remote_payload_store,
            registry=remote_registry,
        )
    current_authority = SQLCurrentKnowledgeIndexAuthorityAdapter(engine)
    execution_binding = KnowledgeIndexExecutionBindingService(
        repository=SQLKnowledgeIndexExecutionRepository(engine),
        authority=current_authority,
    )
    payload_store = IngestionKnowledgeIndexPayloadStore(
        ingestion=core.ingestion_service,
        artifact_repository=repositories.artifact_repo,
    )
    worker_artifacts = KnowledgeIndexWorkerArtifactService(
        knowledge_index_repository=repositories.knowledge_index_repo,
        knowledge_index_run_repository=repositories.knowledge_index_run_repo,
    )
    source_control_completion = (
        KnowledgeIndexSourceControlCompletionProjector(
            repository=SQLSourceControlRepository(engine)
        )
    )
    job_service = build_strict_governed_knowledge_index_job_service(
        StrictGovernedKnowledgeIndexDependencies(
            destination_catalog=destination_catalog,
            source_control_engine=engine,
            signing_key=signing_key,
            execution_binding_service=execution_binding,
            task_queue=core.task_queue_service,
            task_repository=repositories.task_repo,
            payload_store=payload_store,
            worker_artifact_service=worker_artifacts,
            source_control_completion_projector=(
                source_control_completion
            ),
        )
    )
    planner = BoundSourceRevisionAuthorityPlanner(
        revisions=SQLBoundSourceRevisionAuthorityAdapter(engine),
        payloads=RegisteredWorkspaceBoundSourcePayloadAdapter(
            engine=engine,
            workspace_catalog=workspace_catalog,
            workspace_connector=workspace_connector,
            scanner=scanner,
            budgets=budgets,
            remote_payloads=remote_payload_adapter,
        ),
        authority=SQLBoundSourceIndexAuthorityAdapter(
            engine=engine,
            destinations=destination_catalog,
        ),
        destinations=SourceDestinationResolutionService(destination_catalog),
        grants=SQLSourceAccessEnforcementAdapter(
            engine, allow_legacy_reusable_grants=False
        ),
    )
    app.extensions["core_services"] = replace(
        core,
        knowledge=replace(
            core.knowledge,
            knowledge_index_job_service=job_service,
        ),
    )
    return SourceControlIndexProductionComposition(
        planner=planner,
        job_service=job_service,
        execution_binding_service=execution_binding,
        payload_store=payload_store,
        worker_artifact_service=worker_artifacts,
    )


__all__ = [
    "IngestionKnowledgeIndexPayloadStore",
    "RegisteredWorkspaceBoundSourcePayloadAdapter",
    "SQLBoundSourceIndexAuthorityAdapter",
    "SQLBoundSourceRevisionAuthorityAdapter",
    "SQLCurrentKnowledgeIndexAuthorityAdapter",
    "SourceControlIndexProductionComposition",
    "build_source_control_index_production_composition",
]
