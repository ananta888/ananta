"""Hub-only authority, lease and result gate for knowledge-index v2 jobs."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, replace
from typing import Protocol

from ananta_contracts.knowledge_index_execution import (
    KnowledgeIndexAuthorityBinding,
    KnowledgeIndexExecutionAssignment,
    KnowledgeIndexExecutionJob,
    KnowledgeIndexExecutionPayload,
    KnowledgeIndexExecutionResult,
    KnowledgeIndexFileManifest,
    KnowledgeIndexPayloadArtifactRef,
    KnowledgeIndexResourceBudget,
    parse_execution_result,
)


class KnowledgeIndexExecutionBindingError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class CurrentKnowledgeIndexAuthority:
    tenant_id: str
    project_id: str
    source_revision_id: str
    source_revision_digest: str
    admission_digest: str
    policy_snapshot_id: str
    policy_snapshot_digest: str
    destination_id: str
    destination_digest: str
    source_access_grant_id: str
    source_access_grant_digest: str

    def to_binding(self) -> KnowledgeIndexAuthorityBinding:
        return KnowledgeIndexAuthorityBinding.create(**self.__dict__)


class KnowledgeIndexAuthoritySnapshotPort(Protocol):
    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        destination_id: str,
        source_access_grant_id: str,
    ) -> CurrentKnowledgeIndexAuthority: ...


@dataclass(frozen=True)
class KnowledgeIndexExecutionRecord:
    job: KnowledgeIndexExecutionJob
    owner_id: str
    state: str
    lock_version: int
    result_digest: str | None
    updated_at_epoch_ms: int
    completed_at_epoch_ms: int | None = None


class KnowledgeIndexExecutionRepositoryPort(Protocol):
    def admit(
        self,
        record: KnowledgeIndexExecutionRecord,
    ) -> tuple[KnowledgeIndexExecutionRecord, bool]: ...

    def get(self, job_id: str) -> KnowledgeIndexExecutionRecord | None: ...

    def compare_and_set(
        self,
        record: KnowledgeIndexExecutionRecord,
        *,
        expected_lock_version: int,
    ) -> KnowledgeIndexExecutionRecord: ...


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


class KnowledgeIndexExecutionBindingService:
    """Only this Hub service may issue, retry, cancel or finalize v2 jobs."""

    def __init__(
        self,
        *,
        repository: KnowledgeIndexExecutionRepositoryPort,
        authority: KnowledgeIndexAuthoritySnapshotPort,
        clock_ms=lambda: int(time.time() * 1000),
    ) -> None:
        self._repository = repository
        self._authority = authority
        self._clock_ms = clock_ms

    def issue(
        self,
        *,
        hub_task_id: str,
        owner_id: str,
        idempotency_key_digest: str,
        authority: CurrentKnowledgeIndexAuthority,
        files: list[dict],
        resources: KnowledgeIndexResourceBudget,
        payload_artifact_ref: dict,
        assignment: KnowledgeIndexExecutionAssignment,
        scope_id: str,
        source_scope: str,
        profile_name: str,
        created_by: str,
    ) -> KnowledgeIndexExecutionRecord:
        self._assert_current(authority)
        if assignment.lease_expires_epoch_ms <= self._clock_ms():
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_assignment_lease_expired"
            )
        manifest = KnowledgeIndexFileManifest.create(files)
        payload = KnowledgeIndexExecutionPayload(
            payload_artifact_ref=KnowledgeIndexPayloadArtifactRef.model_validate(
                payload_artifact_ref
            )
        )
        job = KnowledgeIndexExecutionJob.create(
            hub_task_id=hub_task_id,
            job_type="source_records",
            scope_id=scope_id,
            source_scope=source_scope,
            profile_name=profile_name,
            created_by=created_by,
            created_at_epoch_ms=int(self._clock_ms()),
            attempt=1,
            idempotency_key_digest=idempotency_key_digest,
            authority_binding=authority.to_binding(),
            file_manifest=manifest,
            resources=resources,
            assignment=assignment,
            payload=payload,
        )
        record, _created = self._repository.admit(
            KnowledgeIndexExecutionRecord(
                job=job,
                owner_id=owner_id,
                state="assigned",
                lock_version=1,
                result_digest=None,
                updated_at_epoch_ms=int(self._clock_ms()),
            )
        )
        return record

    def validate_before_dispatch(
        self,
        *,
        job_id: str,
        authenticated_worker_id: str,
    ) -> KnowledgeIndexExecutionRecord:
        record = self._require(job_id)
        self._assert_live_assignment(
            record,
            authenticated_worker_id=authenticated_worker_id,
        )
        self._assert_current_binding(record.job.authority_binding)
        if record.state not in {"assigned", "running"}:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_not_dispatchable"
            )
        return record

    def mark_running(
        self,
        *,
        job_id: str,
        authenticated_worker_id: str,
        expected_lock_version: int,
    ) -> KnowledgeIndexExecutionRecord:
        current = self.validate_before_dispatch(
            job_id=job_id,
            authenticated_worker_id=authenticated_worker_id,
        )
        if current.lock_version != expected_lock_version:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_version_conflict"
            )
        if current.state == "running":
            return current
        return self._repository.compare_and_set(
            replace(
                current,
                state="running",
                lock_version=current.lock_version + 1,
                updated_at_epoch_ms=int(self._clock_ms()),
            ),
            expected_lock_version=current.lock_version,
        )

    def validate_result(
        self,
        *,
        job_id: str,
        payload: dict,
        authenticated_worker_id: str,
    ) -> tuple[
        KnowledgeIndexExecutionRecord,
        KnowledgeIndexExecutionResult,
    ]:
        result = parse_execution_result(payload)
        record = self._require(job_id)
        job = record.job
        self._assert_live_assignment(
            record,
            authenticated_worker_id=authenticated_worker_id,
        )
        self._assert_current_binding(job.authority_binding)
        if record.state not in {"assigned", "running"}:
            if record.state in {"completed", "failed"}:
                digest = _digest(result.to_wire())
                if digest == record.result_digest:
                    return record, result
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_result_state_invalid"
            )
        expected = {
            "job_id": job.job_id,
            "idempotency_fingerprint": job.idempotency_fingerprint,
            "assignment_id": job.assignment.assignment_id,
            "worker_id": job.assignment.worker_id,
            "lease_id": job.assignment.lease_id,
            "lease_generation": job.assignment.lease_generation,
            "source_revision_id": (
                job.authority_binding.source_revision_id
            ),
            "source_revision_digest": (
                job.authority_binding.source_revision_digest
            ),
            "admission_digest": job.authority_binding.admission_digest,
            "policy_snapshot_id": (
                job.authority_binding.policy_snapshot_id
            ),
            "policy_snapshot_digest": (
                job.authority_binding.policy_snapshot_digest
            ),
            "destination_id": job.authority_binding.destination_id,
            "destination_digest": (
                job.authority_binding.destination_digest
            ),
            "source_access_grant_id": (
                job.authority_binding.source_access_grant_id
            ),
            "source_access_grant_digest": (
                job.authority_binding.source_access_grant_digest
            ),
            "authority_binding_digest": (
                job.authority_binding.binding_digest
            ),
            "file_manifest_digest": job.file_manifest.manifest_digest,
        }
        wire = result.to_wire()
        for field, value in expected.items():
            if wire[field] != value:
                raise KnowledgeIndexExecutionBindingError(
                    f"knowledge_index_execution_result_{field}_stale"
                )
        return record, result

    def finalize_result(
        self,
        *,
        job_id: str,
        payload: dict,
        authenticated_worker_id: str,
    ) -> KnowledgeIndexExecutionRecord:
        record, result = self.validate_result(
            job_id=job_id,
            payload=payload,
            authenticated_worker_id=authenticated_worker_id,
        )
        digest = _digest(result.to_wire())
        if record.state in {"completed", "failed"}:
            return record
        return self._repository.compare_and_set(
            replace(
                record,
                state=result.status,
                lock_version=record.lock_version + 1,
                result_digest=digest,
                updated_at_epoch_ms=int(self._clock_ms()),
                completed_at_epoch_ms=int(self._clock_ms()),
            ),
            expected_lock_version=record.lock_version,
        )

    def request_cancel(
        self,
        *,
        job_id: str,
        expected_lock_version: int,
    ) -> KnowledgeIndexExecutionRecord:
        record = self._require(job_id)
        if (
            record.lock_version != expected_lock_version
            or record.state not in {"assigned", "running"}
        ):
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_cancel_conflict"
            )
        return self._repository.compare_and_set(
            replace(
                record,
                state="cancel_requested",
                lock_version=record.lock_version + 1,
                updated_at_epoch_ms=int(self._clock_ms()),
            ),
            expected_lock_version=record.lock_version,
        )

    def retry(
        self,
        *,
        job_id: str,
        assignment: KnowledgeIndexExecutionAssignment,
        expected_lock_version: int,
    ) -> KnowledgeIndexExecutionRecord:
        record = self._require(job_id)
        if (
            record.lock_version != expected_lock_version
            or record.state not in {"failed", "cancelled"}
            or assignment.lease_generation
            != record.job.assignment.lease_generation + 1
        ):
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_retry_conflict"
            )
        self._assert_current_binding(record.job.authority_binding)
        job = record.job.model_copy(
            update={
                "assignment": assignment,
                "attempt": record.job.attempt + 1,
            }
        )
        return self._repository.compare_and_set(
            replace(
                record,
                job=job,
                state="assigned",
                lock_version=record.lock_version + 1,
                result_digest=None,
                updated_at_epoch_ms=int(self._clock_ms()),
                completed_at_epoch_ms=None,
            ),
            expected_lock_version=record.lock_version,
        )

    def _require(self, job_id: str) -> KnowledgeIndexExecutionRecord:
        record = self._repository.get(job_id)
        if record is None:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_not_found"
            )
        return record

    def _assert_live_assignment(
        self,
        record: KnowledgeIndexExecutionRecord,
        *,
        authenticated_worker_id: str,
    ) -> None:
        assignment = record.job.assignment
        if (
            authenticated_worker_id != assignment.worker_id
            or int(self._clock_ms()) >= assignment.lease_expires_epoch_ms
        ):
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_lease_stale"
            )

    def _assert_current(
        self,
        expected: CurrentKnowledgeIndexAuthority,
    ) -> None:
        current = self._authority.resolve(
            tenant_id=expected.tenant_id,
            project_id=expected.project_id,
            source_revision_id=expected.source_revision_id,
            destination_id=expected.destination_id,
            source_access_grant_id=expected.source_access_grant_id,
        )
        if current != expected:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_authority_stale"
            )

    def _assert_current_binding(
        self,
        binding: KnowledgeIndexAuthorityBinding,
    ) -> None:
        self._assert_current(
            CurrentKnowledgeIndexAuthority(
                tenant_id=binding.tenant_id,
                project_id=binding.project_id,
                source_revision_id=binding.source_revision_id,
                source_revision_digest=binding.source_revision_digest,
                admission_digest=binding.admission_digest,
                policy_snapshot_id=binding.policy_snapshot_id,
                policy_snapshot_digest=binding.policy_snapshot_digest,
                destination_id=binding.destination_id,
                destination_digest=binding.destination_digest,
                source_access_grant_id=binding.source_access_grant_id,
                source_access_grant_digest=(
                    binding.source_access_grant_digest
                ),
            )
        )
