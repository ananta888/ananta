"""Hub-only authority, lease and result gate for knowledge-index v2 jobs."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, replace
from typing import Any, Protocol

from ananta_contracts.knowledge_index_execution import (
    KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS,
    KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON,
    KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
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


@dataclass(frozen=True)
class KnowledgeIndexCompletionProjectionRecord:
    job_id: str
    state: str
    lock_version: int
    projection_digest: str
    payload: dict[str, Any]
    created_at_epoch_ms: int
    updated_at_epoch_ms: int
    projected_at_epoch_ms: int | None = None


class KnowledgeIndexExecutionRepositoryPort(Protocol):
    def admit(
        self,
        record: KnowledgeIndexExecutionRecord,
    ) -> tuple[KnowledgeIndexExecutionRecord, bool]: ...

    def get(self, job_id: str) -> KnowledgeIndexExecutionRecord | None: ...

    def get_by_idempotency(
        self,
        *,
        tenant_id: str,
        project_id: str,
        idempotency_key_digest: str,
    ) -> KnowledgeIndexExecutionRecord | None: ...

    def get_by_assignment(
        self,
        *,
        assignment_id: str,
        lease_id: str,
    ) -> KnowledgeIndexExecutionRecord | None: ...

    def compare_and_set(
        self,
        record: KnowledgeIndexExecutionRecord,
        *,
        expected_lock_version: int,
    ) -> KnowledgeIndexExecutionRecord: ...

    def complete_with_projection(
        self,
        *,
        record: KnowledgeIndexExecutionRecord,
        expected_lock_version: int,
        projection_digest: str,
        projection_payload: dict[str, Any],
        now_epoch_ms: int,
    ) -> tuple[
        KnowledgeIndexExecutionRecord,
        KnowledgeIndexCompletionProjectionRecord,
    ]: ...

    def get_completion_projection(
        self,
        job_id: str,
    ) -> KnowledgeIndexCompletionProjectionRecord | None: ...

    def mark_completion_projection_projected(
        self,
        *,
        job_id: str,
        expected_lock_version: int,
        expected_projection_digest: str,
        now_epoch_ms: int,
    ) -> KnowledgeIndexCompletionProjectionRecord: ...


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


_EXPIRED_DISPATCH_RECEIPT_SCHEMA = (
    "ananta.knowledge_index_execution_expired_dispatch.v1"
)
_COMPLETION_PROJECTION_SCHEMA = (
    "ananta.knowledge_index.completion-projection.v1"
)
_MAX_COMPLETION_PROJECTION_BYTES = 2 * 1024 * 1024


class KnowledgeIndexExecutionBindingService:
    """Hub authority for admission, dispatch, reconciliation and results."""

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
        prepared = self.prepare_issue(
            hub_task_id=hub_task_id,
            owner_id=owner_id,
            idempotency_key_digest=idempotency_key_digest,
            authority=authority,
            files=files,
            resources=resources,
            payload_artifact_ref=payload_artifact_ref,
            assignment=assignment,
            scope_id=scope_id,
            source_scope=source_scope,
            profile_name=profile_name,
            created_by=created_by,
        )
        return self.admit_prepared_issue(prepared)

    def prepare_issue(
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
        """Build and contract-validate an admission without persisting it."""

        normalized_owner_id = str(owner_id or "").strip()
        if not normalized_owner_id or len(normalized_owner_id) > 160:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_owner_invalid"
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
        return KnowledgeIndexExecutionRecord(
            job=job,
            owner_id=normalized_owner_id,
            state="assigned",
            lock_version=1,
            result_digest=None,
            updated_at_epoch_ms=int(self._clock_ms()),
        )

    def validate_prepared_issue(
        self,
        record: KnowledgeIndexExecutionRecord,
    ) -> None:
        """Validate mutable Hub authority immediately before persistence."""

        self._assert_current(
            CurrentKnowledgeIndexAuthority(
                tenant_id=record.job.authority_binding.tenant_id,
                project_id=record.job.authority_binding.project_id,
                source_revision_id=(
                    record.job.authority_binding.source_revision_id
                ),
                source_revision_digest=(
                    record.job.authority_binding.source_revision_digest
                ),
                admission_digest=(
                    record.job.authority_binding.admission_digest
                ),
                policy_snapshot_id=(
                    record.job.authority_binding.policy_snapshot_id
                ),
                policy_snapshot_digest=(
                    record.job.authority_binding.policy_snapshot_digest
                ),
                destination_id=(
                    record.job.authority_binding.destination_id
                ),
                destination_digest=(
                    record.job.authority_binding.destination_digest
                ),
                source_access_grant_id=(
                    record.job.authority_binding.source_access_grant_id
                ),
                source_access_grant_digest=(
                    record.job.authority_binding.source_access_grant_digest
                ),
            )
        )
        if record.job.assignment.lease_expires_epoch_ms <= self._clock_ms():
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_assignment_lease_expired"
            )

    def admit_prepared_issue(
        self,
        record: KnowledgeIndexExecutionRecord,
    ) -> KnowledgeIndexExecutionRecord:
        """Revalidate and atomically admit a previously prepared execution."""

        self.validate_prepared_issue(record)
        admitted, _created = self._repository.admit(record)
        if not self.same_submission(admitted, record):
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_idempotency_conflict"
            )
        return admitted

    def get_by_idempotency(
        self,
        *,
        tenant_id: str,
        project_id: str,
        idempotency_key_digest: str,
    ) -> KnowledgeIndexExecutionRecord | None:
        return self._repository.get_by_idempotency(
            tenant_id=str(tenant_id),
            project_id=str(project_id),
            idempotency_key_digest=str(idempotency_key_digest),
        )

    @staticmethod
    def same_submission(
        existing: KnowledgeIndexExecutionRecord,
        candidate: KnowledgeIndexExecutionRecord,
    ) -> bool:
        """Compare every immutable admission input except creation time."""

        existing_job = existing.job.to_wire()
        candidate_job = candidate.job.to_wire()
        existing_job.pop("created_at_epoch_ms", None)
        candidate_job.pop("created_at_epoch_ms", None)
        return bool(
            existing.owner_id == candidate.owner_id
            and existing_job == candidate_job
        )

    def get_record(self, job_id: str) -> KnowledgeIndexExecutionRecord:
        """Return the durable Hub execution projection without mutation."""

        return self._require(job_id)

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

    def validate_delegated_payload_access(
        self,
        *,
        assignment_id: str,
        lease_id: str,
        authenticated_worker_id: str,
    ) -> KnowledgeIndexExecutionRecord:
        """Validate post-dispatch payload access without re-consuming authority.

        The signed enforcement manifest carries the Hub decision made at
        dispatch. A one-time grant has already been consumed at that point, so
        this phase deliberately validates only the immutable binding, assigned
        Worker, live lease and dispatchable state.
        """

        record = self._repository.get_by_assignment(
            assignment_id=str(assignment_id),
            lease_id=str(lease_id),
        )
        if record is None:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_not_found"
            )
        self._assert_live_assignment(
            record,
            authenticated_worker_id=authenticated_worker_id,
        )
        self._assert_current_binding(record.job.authority_binding)
        if record.state != "running":
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_payload_access_invalid"
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

    def claim_dispatch(
        self,
        *,
        job_id: str,
        authenticated_worker_id: str,
        expected_lock_version: int,
    ) -> KnowledgeIndexExecutionRecord:
        """Atomically claim an assigned execution before Worker transport.

        Unlike :meth:`mark_running`, this Hub-owned dispatch gate is strict:
        a running execution is already owned by another dispatcher and must
        never be transported a second time. ``mark_running`` intentionally
        retains its idempotent legacy behavior for existing callers.
        """

        current = self.validate_before_dispatch(
            job_id=job_id,
            authenticated_worker_id=authenticated_worker_id,
        )
        required_window_ms = (
            current.job.resources.max_runtime_seconds
            + KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS
        ) * 1000
        if (
            current.job.assignment.lease_expires_epoch_ms
            - int(self._clock_ms())
            < required_window_ms
        ):
            raise KnowledgeIndexExecutionBindingError(
                KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON
            )
        self._assert_dispatch_claimable(
            current,
            expected_lock_version=expected_lock_version,
        )
        try:
            return self._repository.compare_and_set(
                replace(
                    current,
                    state="running",
                    lock_version=current.lock_version + 1,
                    updated_at_epoch_ms=int(self._clock_ms()),
                ),
                expected_lock_version=current.lock_version,
            )
        except KnowledgeIndexExecutionBindingError as exc:
            if (
                exc.reason_code
                != "knowledge_index_execution_version_conflict"
            ):
                raise
            self._raise_dispatch_claim_conflict(
                job_id=job_id,
                authenticated_worker_id=authenticated_worker_id,
            )
            raise

    def reconcile_expired_dispatch(
        self,
        *,
        job_id: str,
        expected_lock_version: int,
    ) -> KnowledgeIndexExecutionRecord:
        """Close an expired running dispatch without executing it again.

        A ``running`` record is the durable at-most-once dispatch claim.  If
        the Hub crashes after claiming it, or loses the Worker response, that
        claim remains exclusive until its assignment lease expires.  The Hub
        may then CAS the record to a terminal failure tombstone.  Reconciliation
        never creates a new assignment and never authorizes another transport.

        Current authority is deliberately not required for this closing
        transition: revocation or policy rotation must not leave an expired
        execution stuck in ``running``.  The tombstone itself is bound to the
        immutable authority and assignment digests of the admitted job.
        """

        current = self._require(job_id)
        if current.lock_version != expected_lock_version:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_reconcile_conflict"
            )
        if self._is_expired_dispatch_tombstone(current):
            return current
        if current.state != "running":
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_not_reconcilable"
            )
        now_ms = int(self._clock_ms())
        if now_ms < current.job.assignment.lease_expires_epoch_ms:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_dispatch_lease_active"
            )
        replacement = replace(
            current,
            state="failed",
            lock_version=current.lock_version + 1,
            result_digest=self._expired_dispatch_tombstone_digest(current),
            updated_at_epoch_ms=now_ms,
            completed_at_epoch_ms=now_ms,
        )
        try:
            return self._repository.compare_and_set(
                replacement,
                expected_lock_version=current.lock_version,
            )
        except KnowledgeIndexExecutionBindingError as exc:
            if exc.reason_code != "knowledge_index_execution_version_conflict":
                raise
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_reconcile_conflict"
            ) from None

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
        if self._is_expired_dispatch_tombstone(record):
            # A reconciled lost-response claim is a permanent, non-replayable
            # terminal authority decision.  It must never be confused with a
            # Worker-produced failed result merely because both use the same
            # lifecycle state.
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_lease_stale"
            )
        if record.state in {"completed", "failed"}:
            # A terminal record is the durable authority decision.  Exact
            # result replay by its originally authenticated Worker must remain
            # possible after the one-time lease or mutable source authority
            # expires, so a Hub crash between the result CAS and its
            # idempotent completion projection can be recovered.
            if authenticated_worker_id != job.assignment.worker_id:
                raise KnowledgeIndexExecutionBindingError(
                    "knowledge_index_execution_lease_stale"
                )
            if _digest(result.to_wire()) == record.result_digest:
                return record, result
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_result_state_invalid"
            )
        self._assert_live_assignment(
            record,
            authenticated_worker_id=authenticated_worker_id,
        )
        self._assert_current_binding(job.authority_binding)
        if record.state != "running":
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

    def finalize_completed_result_with_projection(
        self,
        *,
        job_id: str,
        worker_result: dict[str, Any],
        materialized_result: dict[str, Any],
        authenticated_worker_id: str,
    ) -> tuple[
        KnowledgeIndexExecutionRecord,
        KnowledgeIndexCompletionProjectionRecord,
    ]:
        """Atomically accept a completed result and stage its Hub outbox."""

        record, parsed = self.validate_result(
            job_id=job_id,
            payload=worker_result,
            authenticated_worker_id=authenticated_worker_id,
        )
        wire = parsed.to_wire()
        if str(wire.get("status") or "") != "completed":
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_result_incomplete"
            )
        projection_payload, projection_digest = (
            self._completion_projection_candidate(
                job_id=str(job_id),
                worker_result=wire,
                materialized_result=materialized_result,
            )
        )
        result_digest = _digest(wire)
        if record.state == "completed":
            projection = self.get_completion_projection(str(job_id))
            if (
                record.result_digest == result_digest
                and projection.projection_digest == projection_digest
                and projection.payload == projection_payload
            ):
                return record, projection
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_conflict"
            )
        if record.state != "running":
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_result_state_invalid"
            )
        commit = getattr(
            self._repository,
            "complete_with_projection",
            None,
        )
        if not callable(commit):
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_store_unavailable"
            )
        now_epoch_ms = int(self._clock_ms())
        try:
            return commit(
                record=replace(
                    record,
                    state="completed",
                    lock_version=record.lock_version + 1,
                    result_digest=result_digest,
                    updated_at_epoch_ms=now_epoch_ms,
                    completed_at_epoch_ms=now_epoch_ms,
                ),
                expected_lock_version=record.lock_version,
                projection_digest=projection_digest,
                projection_payload=projection_payload,
                now_epoch_ms=now_epoch_ms,
            )
        except Exception:
            # A database adapter may lose the return value after committing
            # the atomic result/outbox UPDATE.  Reload both durable records
            # and converge only when every submitted digest and byte-shaped
            # projection input matches exactly.  Conflicts and partial writes
            # retain the original exception and remain non-accepted.
            recovered = self._recover_exact_completion_commit(
                job_id=str(job_id),
                result_digest=result_digest,
                projection_digest=projection_digest,
                projection_payload=projection_payload,
            )
            if recovered is not None:
                return recovered
            raise

    def _recover_exact_completion_commit(
        self,
        *,
        job_id: str,
        result_digest: str,
        projection_digest: str,
        projection_payload: dict[str, Any],
    ) -> tuple[
        KnowledgeIndexExecutionRecord,
        KnowledgeIndexCompletionProjectionRecord,
    ] | None:
        """Converge an ambiguous commit only from exact durable evidence."""

        try:
            record = self._repository.get(str(job_id))
            projection = self.get_completion_projection(str(job_id))
        except Exception:
            return None
        if (
            record is not None
            and record.state == "completed"
            and record.result_digest == str(result_digest)
            and projection.state in {"pending", "projected"}
            and projection.projection_digest == str(projection_digest)
            and projection.payload == dict(projection_payload)
        ):
            return record, projection
        return None

    def get_completion_projection(
        self,
        job_id: str,
        *,
        require_terminal_result: bool = True,
    ) -> KnowledgeIndexCompletionProjectionRecord:
        """Load a closed internal projection receipt without exposing it."""

        getter = getattr(
            self._repository,
            "get_completion_projection",
            None,
        )
        if not callable(getter):
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_store_unavailable"
            )
        projection = getter(str(job_id))
        if projection is None:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_not_found"
            )
        digest = self._validate_completion_projection_payload(
            projection.payload,
            job_id=str(job_id),
        )
        if digest != projection.projection_digest:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_digest_invalid"
            )
        if require_terminal_result:
            record = self._require(str(job_id))
            worker_result_digest = str(
                projection.payload.get("worker_result_digest") or ""
            )
            if (
                record.state != "completed"
                or record.result_digest != worker_result_digest
            ):
                raise KnowledgeIndexExecutionBindingError(
                    "knowledge_index_completion_projection_not_ready"
                )
        return projection

    def mark_completion_projection_projected(
        self,
        *,
        job_id: str,
        expected_lock_version: int,
        expected_projection_digest: str,
    ) -> KnowledgeIndexCompletionProjectionRecord:
        projection = self.get_completion_projection(str(job_id))
        if (
            projection.projection_digest
            != str(expected_projection_digest)
        ):
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_conflict"
            )
        marker = getattr(
            self._repository,
            "mark_completion_projection_projected",
            None,
        )
        if not callable(marker):
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_store_unavailable"
            )
        return marker(
            job_id=str(job_id),
            expected_lock_version=int(expected_lock_version),
            expected_projection_digest=str(
                expected_projection_digest
            ),
            now_epoch_ms=int(self._clock_ms()),
        )

    @staticmethod
    def _completion_projection_candidate(
        *,
        job_id: str,
        worker_result: dict[str, Any],
        materialized_result: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        payload = {
            "schema": _COMPLETION_PROJECTION_SCHEMA,
            "job_id": str(job_id),
            "worker_result_digest": _digest(worker_result),
            "materialized_result": dict(materialized_result),
            "artifact_references": [
                dict(item)
                for item in list(worker_result.get("artifact_refs") or [])
            ],
        }
        projection_digest = (
            KnowledgeIndexExecutionBindingService
            ._validate_completion_projection_payload(
                payload,
                job_id=str(job_id),
            )
        )
        return payload, projection_digest

    @staticmethod
    def _validate_completion_projection_payload(
        payload: dict[str, Any],
        *,
        job_id: str,
    ) -> str:
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "job_id",
            "worker_result_digest",
            "materialized_result",
            "artifact_references",
        }:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_payload_invalid"
            )
        materialized = payload.get("materialized_result")
        references = payload.get("artifact_references")
        worker_result_digest = str(
            payload.get("worker_result_digest") or ""
        )
        if (
            payload.get("schema") != _COMPLETION_PROJECTION_SCHEMA
            or str(payload.get("job_id") or "") != job_id
            or len(worker_result_digest) != 64
            or any(
                char not in "0123456789abcdef"
                for char in worker_result_digest
            )
            or not isinstance(materialized, dict)
            or str(materialized.get("status") or "") != "completed"
            or not isinstance(references, list)
            or len(references) > 6
            or any(not isinstance(item, dict) for item in references)
        ):
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_payload_invalid"
            )
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        except (TypeError, ValueError) as exc:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_payload_invalid"
            ) from exc
        if len(encoded) > _MAX_COMPLETION_PROJECTION_BYTES:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_payload_too_large"
            )
        return hashlib.sha256(encoded).hexdigest()

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
        """Reject in-place v2 retry because authority is one-time bound."""

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
        # Every v2 execution is bound to the exact admitted source-access
        # grant.  That grant is one-time, so replacing only the assignment
        # would reuse consumed authority and make a second transport possible.
        # A retry therefore requires normal admission of a new job with a new
        # grant and idempotency key.
        raise KnowledgeIndexExecutionBindingError(
            "knowledge_index_retry_requires_fresh_grant"
        )

    def _require(self, job_id: str) -> KnowledgeIndexExecutionRecord:
        record = self._repository.get(job_id)
        if record is None:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_not_found"
            )
        return record

    @staticmethod
    def _assert_dispatch_claimable(
        record: KnowledgeIndexExecutionRecord,
        *,
        expected_lock_version: int,
    ) -> None:
        if record.state == "running":
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_dispatch_in_progress"
            )
        if record.state != "assigned":
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_not_dispatchable"
            )
        if record.lock_version != expected_lock_version:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_version_conflict"
            )

    def _raise_dispatch_claim_conflict(
        self,
        *,
        job_id: str,
        authenticated_worker_id: str,
    ) -> None:
        latest = self._require(job_id)
        if latest.state == "running":
            self._assert_live_assignment(
                latest,
                authenticated_worker_id=authenticated_worker_id,
            )
            self._assert_current_binding(latest.job.authority_binding)
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_dispatch_in_progress"
            )
        if latest.state != "assigned":
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_not_dispatchable"
            )

    @staticmethod
    def _expired_dispatch_tombstone_digest(
        record: KnowledgeIndexExecutionRecord,
    ) -> str:
        job = record.job
        assignment = job.assignment
        return _digest(
            {
                "schema": _EXPIRED_DISPATCH_RECEIPT_SCHEMA,
                "reason_code": KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
                "job_id": job.job_id,
                "idempotency_fingerprint": job.idempotency_fingerprint,
                "authority_binding_digest": (
                    job.authority_binding.binding_digest
                ),
                "assignment_id": assignment.assignment_id,
                "worker_id": assignment.worker_id,
                "lease_id": assignment.lease_id,
                "lease_generation": assignment.lease_generation,
                "lease_expires_epoch_ms": (
                    assignment.lease_expires_epoch_ms
                ),
            }
        )

    @classmethod
    def _is_expired_dispatch_tombstone(
        cls,
        record: KnowledgeIndexExecutionRecord,
    ) -> bool:
        return (
            record.state == "failed"
            and record.result_digest
            == cls._expired_dispatch_tombstone_digest(record)
        )

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
