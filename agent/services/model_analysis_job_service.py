"""Hub-owned model-analysis admission, lifecycle, and recovery."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

from agent.services.model_analysis_task_port import (
    HubModelAnalysisTaskSubmissionPort,
    ModelAnalysisTaskSubmissionPort,
)
from ananta_contracts.model_intelligence import AnalysisJob
from ananta_contracts.model_intelligence_execution import (
    AnalysisCompletion,
    CancellationReason,
    CancellationSignal,
    CompletionOutcome,
    ResourceLease,
)


class ModelAnalysisJobState(str, Enum):
    SUBMISSION_PENDING = "submission_pending"
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {
        ModelAnalysisJobState.SUCCEEDED,
        ModelAnalysisJobState.FAILED,
        ModelAnalysisJobState.CANCELLED,
    }
)
QUEUED_STATES = frozenset(
    {
        ModelAnalysisJobState.SUBMISSION_PENDING,
        ModelAnalysisJobState.QUEUED,
    }
)


class ModelAnalysisJobServiceError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ModelAnalysisLimits:
    max_global_queued: int = 128
    max_tenant_queued: int = 16
    max_tenant_active: int = 32
    max_attempts: int = 3
    max_lease_seconds: int = 3600
    max_worker_memory_bytes: int = 64 * 1024**3

    def __post_init__(self) -> None:
        for value in (
            self.max_global_queued,
            self.max_tenant_queued,
            self.max_tenant_active,
            self.max_attempts,
            self.max_lease_seconds,
            self.max_worker_memory_bytes,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("model_analysis_limits_invalid")


@dataclass(frozen=True, slots=True)
class ModelAnalysisJobRecord:
    job: AnalysisJob
    state: ModelAnalysisJobState
    version: int
    attempt: int
    lease: ResourceLease | None
    completion: AnalysisCompletion | None
    reason_code: str
    projection_pending: bool
    updated_epoch_ms: int


@dataclass(frozen=True, slots=True)
class ModelAnalysisRecoverySummary:
    scanned: int = 0
    recovered: int = 0
    requeued: int = 0
    failed: int = 0
    cancelled: int = 0
    conflicts: int = 0


@dataclass(frozen=True, slots=True)
class ModelAnalysisJobPage:
    items: tuple[ModelAnalysisJobRecord, ...]
    next_cursor: str | None


class ModelAnalysisJobRepository(Protocol):
    def admit(
        self,
        record: ModelAnalysisJobRecord,
        *,
        idempotency_key_digest: str,
        request_digest: str,
        limits: ModelAnalysisLimits,
    ) -> tuple[ModelAnalysisJobRecord, bool]: ...

    def get(self, job_id: str) -> ModelAnalysisJobRecord | None: ...

    def compare_and_set(
        self,
        record: ModelAnalysisJobRecord,
        *,
        expected_version: int,
    ) -> ModelAnalysisJobRecord: ...

    def mark_projected(
        self,
        job_id: str,
        *,
        expected_version: int,
    ) -> ModelAnalysisJobRecord: ...

    def list_recoverable(
        self,
        *,
        now_epoch_ms: int,
        limit: int,
    ) -> tuple[ModelAnalysisJobRecord, ...]: ...

    def list_page(
        self,
        *,
        tenant_id: str,
        after_job_id: str | None,
        limit: int,
    ) -> tuple[ModelAnalysisJobRecord, ...]: ...


class InMemoryModelAnalysisJobRepository:
    """Reference adapter with atomic semantics for tests and local Hub mode."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, ModelAnalysisJobRecord] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}

    def admit(
        self,
        record: ModelAnalysisJobRecord,
        *,
        idempotency_key_digest: str,
        request_digest: str,
        limits: ModelAnalysisLimits,
    ) -> tuple[ModelAnalysisJobRecord, bool]:
        key = (record.job.tenant_id, idempotency_key_digest)
        with self._lock:
            existing_binding = self._idempotency.get(key)
            if existing_binding is not None:
                job_id, existing_digest = existing_binding
                if existing_digest != request_digest:
                    raise ModelAnalysisJobServiceError(
                        "model_analysis_idempotency_conflict"
                    )
                return self._records[job_id], False
            if record.job.job_id in self._records:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_job_id_conflict"
                )
            active = [
                item
                for item in self._records.values()
                if item.state not in TERMINAL_STATES
            ]
            tenant_active = [
                item
                for item in active
                if item.job.tenant_id == record.job.tenant_id
            ]
            queued = [item for item in active if item.state in QUEUED_STATES]
            tenant_queued = [
                item
                for item in queued
                if item.job.tenant_id == record.job.tenant_id
            ]
            if len(queued) >= limits.max_global_queued:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_global_queue_full",
                    retryable=True,
                )
            if len(tenant_queued) >= limits.max_tenant_queued:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_tenant_queue_full",
                    retryable=True,
                )
            if len(tenant_active) >= limits.max_tenant_active:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_tenant_active_limit",
                    retryable=True,
                )
            self._records[record.job.job_id] = record
            self._idempotency[key] = (record.job.job_id, request_digest)
            return record, True

    def get(self, job_id: str) -> ModelAnalysisJobRecord | None:
        with self._lock:
            return self._records.get(job_id)

    def compare_and_set(
        self,
        record: ModelAnalysisJobRecord,
        *,
        expected_version: int,
    ) -> ModelAnalysisJobRecord:
        with self._lock:
            current = self._records.get(record.job.job_id)
            if current is None:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_job_not_found"
                )
            if current.version != expected_version:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_version_conflict",
                    retryable=True,
                )
            if record.version != expected_version + 1:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_version_increment_invalid"
                )
            self._records[record.job.job_id] = record
            return record

    def mark_projected(
        self,
        job_id: str,
        *,
        expected_version: int,
    ) -> ModelAnalysisJobRecord:
        with self._lock:
            current = self._records.get(job_id)
            if current is None:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_job_not_found"
                )
            if current.version != expected_version:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_version_conflict",
                    retryable=True,
                )
            projected = replace(current, projection_pending=False)
            self._records[job_id] = projected
            return projected

    def list_recoverable(
        self,
        *,
        now_epoch_ms: int,
        limit: int,
    ) -> tuple[ModelAnalysisJobRecord, ...]:
        with self._lock:
            candidates = [
                record
                for record in self._records.values()
                if record.state is ModelAnalysisJobState.SUBMISSION_PENDING
                or record.projection_pending
                or (
                    record.state
                    in {
                        ModelAnalysisJobState.RUNNING,
                        ModelAnalysisJobState.CANCEL_REQUESTED,
                    }
                    and record.lease is not None
                    and record.lease.expires_epoch_ms <= now_epoch_ms
                )
            ]
            return tuple(
                sorted(candidates, key=lambda item: item.job.job_id)[:limit]
            )

    def list_page(
        self,
        *,
        tenant_id: str,
        after_job_id: str | None,
        limit: int,
    ) -> tuple[ModelAnalysisJobRecord, ...]:
        with self._lock:
            rows = sorted(
                (
                    record
                    for record in self._records.values()
                    if record.job.tenant_id == tenant_id
                    and (
                        after_job_id is None
                        or record.job.job_id > after_job_id
                    )
                ),
                key=lambda item: item.job.job_id,
            )
            return tuple(rows[:limit])


class ModelAnalysisJobService:
    """Hub state machine and sole owner of model-analysis task submission."""

    def __init__(
        self,
        *,
        repository: ModelAnalysisJobRepository,
        tasks: ModelAnalysisTaskSubmissionPort,
        limits: ModelAnalysisLimits | None = None,
        epoch_ms: Callable[[], int] | None = None,
    ) -> None:
        self._repository = repository
        self._tasks = tasks
        self._limits = limits or ModelAnalysisLimits()
        self._epoch_ms = epoch_ms or (lambda: time.time_ns() // 1_000_000)

    def submit(
        self,
        job: AnalysisJob,
        *,
        idempotency_key: str,
    ) -> ModelAnalysisJobRecord:
        key_digest = self._idempotency_digest(
            tenant_id=job.tenant_id,
            idempotency_key=idempotency_key,
        )
        request_digest = self._digest(job.to_wire())
        now = self._epoch_ms()
        record, created = self._repository.admit(
            ModelAnalysisJobRecord(
                job=job,
                state=ModelAnalysisJobState.SUBMISSION_PENDING,
                version=1,
                attempt=0,
                lease=None,
                completion=None,
                reason_code="model_analysis_submission_pending",
                projection_pending=True,
                updated_epoch_ms=now,
            ),
            idempotency_key_digest=key_digest,
            request_digest=request_digest,
            limits=self._limits,
        )
        if not created and record.state is not ModelAnalysisJobState.SUBMISSION_PENDING:
            return record
        return self._materialize(record)

    def get(self, *, tenant_id: str, job_id: str) -> ModelAnalysisJobRecord:
        record = self._repository.get(job_id)
        if record is None or record.job.tenant_id != tenant_id:
            raise ModelAnalysisJobServiceError("model_analysis_job_not_found")
        return record

    def list_page(
        self,
        *,
        tenant_id: str,
        page_size: int = 50,
        cursor: str | None = None,
    ) -> ModelAnalysisJobPage:
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 100
        ):
            raise ModelAnalysisJobServiceError(
                "model_analysis_page_size_invalid"
            )
        after_job_id = self._decode_cursor(
            tenant_id=tenant_id,
            cursor=cursor,
        )
        rows = self._repository.list_page(
            tenant_id=tenant_id,
            after_job_id=after_job_id,
            limit=page_size + 1,
        )
        items = rows[:page_size]
        next_cursor = None
        if len(rows) > page_size and items:
            next_cursor = self._encode_cursor(
                tenant_id=tenant_id,
                after_job_id=items[-1].job.job_id,
            )
        return ModelAnalysisJobPage(
            items=tuple(items),
            next_cursor=next_cursor,
        )

    def claim(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        expected_version: int,
        lease_seconds: int,
        max_memory_bytes: int,
    ) -> tuple[ModelAnalysisJobRecord, ResourceLease]:
        record = self.get(tenant_id=tenant_id, job_id=job_id)
        if record.version != expected_version:
            raise ModelAnalysisJobServiceError(
                "model_analysis_version_conflict",
                retryable=True,
            )
        if record.state is not ModelAnalysisJobState.QUEUED:
            raise ModelAnalysisJobServiceError(
                "model_analysis_job_not_claimable"
            )
        normalized_worker = self._identifier(
            worker_id,
            "model_analysis_worker_id_invalid",
        )
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds < 1
        ):
            raise ModelAnalysisJobServiceError(
                "model_analysis_lease_duration_invalid"
            )
        if (
            isinstance(max_memory_bytes, bool)
            or not isinstance(max_memory_bytes, int)
            or max_memory_bytes < 1
        ):
            raise ModelAnalysisJobServiceError(
                "model_analysis_memory_budget_invalid"
            )
        now = self._epoch_ms()
        generation = record.attempt + 1
        lease_duration_ms = (
            min(
                lease_seconds,
                self._limits.max_lease_seconds,
                record.job.max_runtime_seconds,
            )
            * 1000
        )
        binding = {
            "job_id": record.job.job_id,
            "generation": generation,
            "request_sha256": record.job.request_sha256,
            "worker_id": normalized_worker,
        }
        lease = ResourceLease(
            lease_id=f"lease-{self._digest(binding)[:48]}",
            job_id=record.job.job_id,
            tenant_id=record.job.tenant_id,
            worker_id=normalized_worker,
            lease_generation=generation,
            acquired_epoch_ms=now,
            expires_epoch_ms=now + lease_duration_ms,
            max_memory_bytes=min(
                max_memory_bytes,
                self._limits.max_worker_memory_bytes,
            ),
            max_output_bytes=record.job.max_output_bytes,
            completion_key=f"completion_{self._digest({**binding, 'purpose': 'completion'})}",
            request_sha256=record.job.request_sha256,
        )
        claimed = self._repository.compare_and_set(
            replace(
                record,
                state=ModelAnalysisJobState.RUNNING,
                version=record.version + 1,
                attempt=generation,
                lease=lease,
                reason_code="model_analysis_worker_claimed",
                projection_pending=True,
                updated_epoch_ms=now,
            ),
            expected_version=record.version,
        )
        return self._project(claimed), lease

    def request_cancel(
        self,
        *,
        tenant_id: str,
        job_id: str,
        expected_version: int,
    ) -> tuple[ModelAnalysisJobRecord, CancellationSignal | None]:
        record = self.get(tenant_id=tenant_id, job_id=job_id)
        if record.version != expected_version:
            raise ModelAnalysisJobServiceError(
                "model_analysis_version_conflict",
                retryable=True,
            )
        if record.state in TERMINAL_STATES:
            return record, None
        now = self._epoch_ms()
        signal: CancellationSignal | None = None
        if record.state is ModelAnalysisJobState.RUNNING:
            if record.lease is None:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_active_lease_missing"
                )
            signal = CancellationSignal(
                job_id=record.job.job_id,
                lease_id=record.lease.lease_id,
                lease_generation=record.lease.lease_generation,
                reason_code=CancellationReason.HUB_CANCELLED,
                requested_epoch_ms=now,
            )
            target = ModelAnalysisJobState.CANCEL_REQUESTED
        else:
            target = ModelAnalysisJobState.CANCELLED
        cancelled = self._repository.compare_and_set(
            replace(
                record,
                state=target,
                version=record.version + 1,
                reason_code="model_analysis_hub_cancelled",
                projection_pending=True,
                updated_epoch_ms=now,
            ),
            expected_version=record.version,
        )
        return self._project(cancelled), signal

    def complete(
        self,
        completion: AnalysisCompletion,
    ) -> ModelAnalysisJobRecord:
        record = self._repository.get(completion.job_id)
        if record is None:
            raise ModelAnalysisJobServiceError("model_analysis_job_not_found")
        if record.completion is not None:
            if record.completion.completion_key == completion.completion_key:
                return self._project(record)
            raise ModelAnalysisJobServiceError(
                "model_analysis_completion_conflict"
            )
        if record.state not in {
            ModelAnalysisJobState.RUNNING,
            ModelAnalysisJobState.CANCEL_REQUESTED,
        }:
            raise ModelAnalysisJobServiceError(
                "model_analysis_completion_state_invalid"
            )
        lease = record.lease
        if (
            lease is None
            or completion.lease_id != lease.lease_id
            or completion.lease_generation != lease.lease_generation
            or completion.completion_key != lease.completion_key
        ):
            raise ModelAnalysisJobServiceError(
                "model_analysis_completion_fence_invalid"
            )
        if self._epoch_ms() >= lease.expires_epoch_ms:
            raise ModelAnalysisJobServiceError(
                "model_analysis_completion_lease_expired"
            )
        if (
            record.state is ModelAnalysisJobState.CANCEL_REQUESTED
            and completion.outcome is not CompletionOutcome.CANCELLED
        ):
            raise ModelAnalysisJobServiceError(
                "model_analysis_completion_after_cancel"
            )
        target = {
            CompletionOutcome.SUCCEEDED: ModelAnalysisJobState.SUCCEEDED,
            CompletionOutcome.FAILED: ModelAnalysisJobState.FAILED,
            CompletionOutcome.CANCELLED: ModelAnalysisJobState.CANCELLED,
        }[completion.outcome]
        reason_code = (
            "model_analysis_succeeded"
            if completion.error is None
            else completion.error.reason_code.value
        )
        completed = self._repository.compare_and_set(
            replace(
                record,
                state=target,
                version=record.version + 1,
                completion=completion,
                reason_code=reason_code,
                projection_pending=True,
                updated_epoch_ms=self._epoch_ms(),
            ),
            expected_version=record.version,
        )
        return self._project(completed)

    def recover(self, *, limit: int = 100) -> ModelAnalysisRecoverySummary:
        if not 1 <= limit <= 1000:
            raise ModelAnalysisJobServiceError(
                "model_analysis_recovery_limit_invalid"
            )
        now = self._epoch_ms()
        candidates = self._repository.list_recoverable(
            now_epoch_ms=now,
            limit=limit,
        )
        summary = ModelAnalysisRecoverySummary(scanned=len(candidates))
        for record in candidates:
            try:
                recovered, disposition = self._recover_one(record, now=now)
                summary = replace(
                    summary,
                    recovered=summary.recovered + int(recovered),
                    requeued=summary.requeued + int(disposition == "requeued"),
                    failed=summary.failed + int(disposition == "failed"),
                    cancelled=summary.cancelled + int(disposition == "cancelled"),
                )
            except ModelAnalysisJobServiceError as exc:
                if exc.reason_code == "model_analysis_version_conflict":
                    summary = replace(
                        summary,
                        conflicts=summary.conflicts + 1,
                    )
        return summary

    def _recover_one(
        self,
        record: ModelAnalysisJobRecord,
        *,
        now: int,
    ) -> tuple[bool, str]:
        if record.state is ModelAnalysisJobState.SUBMISSION_PENDING:
            self._materialize(record)
            return True, "submitted"
        if (
            record.state
            in {
                ModelAnalysisJobState.RUNNING,
                ModelAnalysisJobState.CANCEL_REQUESTED,
            }
            and record.lease is not None
            and record.lease.expires_epoch_ms <= now
        ):
            if record.state is ModelAnalysisJobState.CANCEL_REQUESTED:
                target = ModelAnalysisJobState.CANCELLED
                disposition = "cancelled"
                reason = "model_analysis_cancel_grace_elapsed"
            elif record.attempt < self._limits.max_attempts:
                target = ModelAnalysisJobState.QUEUED
                disposition = "requeued"
                reason = "model_analysis_lease_expired_requeued"
            else:
                target = ModelAnalysisJobState.FAILED
                disposition = "failed"
                reason = "model_analysis_attempts_exhausted"
            recovered = self._repository.compare_and_set(
                replace(
                    record,
                    state=target,
                    version=record.version + 1,
                    lease=None if target is ModelAnalysisJobState.QUEUED else record.lease,
                    reason_code=reason,
                    projection_pending=True,
                    updated_epoch_ms=now,
                ),
                expected_version=record.version,
            )
            self._project(recovered)
            return True, disposition
        if record.projection_pending:
            self._project(record)
            return True, "projected"
        return False, "none"

    def _materialize(
        self,
        record: ModelAnalysisJobRecord,
    ) -> ModelAnalysisJobRecord:
        try:
            self._tasks.submit(record.job)
        except Exception as exc:
            raise ModelAnalysisJobServiceError(
                "model_analysis_task_submission_unavailable",
                retryable=True,
            ) from exc
        try:
            return self._repository.compare_and_set(
                replace(
                    record,
                    state=ModelAnalysisJobState.QUEUED,
                    version=record.version + 1,
                    reason_code="model_analysis_queued",
                    projection_pending=False,
                    updated_epoch_ms=self._epoch_ms(),
                ),
                expected_version=record.version,
            )
        except ModelAnalysisJobServiceError as exc:
            if exc.reason_code != "model_analysis_version_conflict":
                raise
            current = self._repository.get(record.job.job_id)
            if current is None:
                raise
            return current

    def _project(
        self,
        record: ModelAnalysisJobRecord,
    ) -> ModelAnalysisJobRecord:
        if not record.projection_pending:
            return record
        try:
            if record.state is ModelAnalysisJobState.QUEUED:
                self._tasks.submit(record.job)
            elif record.state is ModelAnalysisJobState.RUNNING:
                if record.lease is None:
                    raise ModelAnalysisJobServiceError(
                        "model_analysis_active_lease_missing"
                    )
                self._tasks.mark_running(
                    record.job,
                    worker_id=record.lease.worker_id,
                )
            elif record.state is ModelAnalysisJobState.CANCEL_REQUESTED:
                self._tasks.mark_cancel_requested(
                    record.job,
                    reason_code=record.reason_code,
                )
            elif record.state in TERMINAL_STATES:
                self._tasks.finish(
                    record.job,
                    status=record.state.value,
                    reason_code=record.reason_code,
                )
        except ModelAnalysisJobServiceError:
            raise
        except Exception:
            return record
        return self._repository.mark_projected(
            record.job.job_id,
            expected_version=record.version,
        )

    @staticmethod
    def _idempotency_digest(
        *,
        tenant_id: str,
        idempotency_key: str,
    ) -> str:
        key = str(idempotency_key or "").strip()
        if (
            not 8 <= len(key) <= 256
            or any(not 0x21 <= ord(character) <= 0x7E for character in key)
        ):
            raise ModelAnalysisJobServiceError(
                "model_analysis_idempotency_key_invalid"
            )
        return ModelAnalysisJobService._digest(
            {
                "schema": "ananta.model-analysis-idempotency.v1",
                "tenant_id": tenant_id,
                "idempotency_key": key,
            }
        )

    @staticmethod
    def _identifier(value: str, reason_code: str) -> str:
        normalized = str(value or "").strip()
        if (
            not 1 <= len(normalized) <= 128
            or any(character.isspace() for character in normalized)
        ):
            raise ModelAnalysisJobServiceError(reason_code)
        return normalized

    @staticmethod
    def _encode_cursor(*, tenant_id: str, after_job_id: str) -> str:
        payload = json.dumps(
            {
                "after_job_id": after_job_id,
                "tenant_scope": hashlib.sha256(
                    tenant_id.encode("utf-8")
                ).hexdigest(),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(
        *,
        tenant_id: str,
        cursor: str | None,
    ) -> str | None:
        if cursor is None:
            return None
        normalized = str(cursor).strip()
        if not normalized or len(normalized) > 1024:
            raise ModelAnalysisJobServiceError(
                "model_analysis_cursor_invalid"
            )
        try:
            padding = "=" * (-len(normalized) % 4)
            raw = json.loads(
                base64.urlsafe_b64decode(normalized + padding).decode("ascii")
            )
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise ModelAnalysisJobServiceError(
                "model_analysis_cursor_invalid"
            ) from exc
        expected_scope = hashlib.sha256(
            tenant_id.encode("utf-8")
        ).hexdigest()
        if (
            not isinstance(raw, dict)
            or set(raw) != {"after_job_id", "tenant_scope"}
            or raw.get("tenant_scope") != expected_scope
        ):
            raise ModelAnalysisJobServiceError(
                "model_analysis_cursor_invalid"
            )
        return ModelAnalysisJobService._identifier(
            str(raw.get("after_job_id") or ""),
            "model_analysis_cursor_invalid",
        )

    @staticmethod
    def _digest(value: object) -> str:
        canonical = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(canonical).hexdigest()


def build_model_analysis_job_service(
    *,
    repository: ModelAnalysisJobRepository | None = None,
    tasks: ModelAnalysisTaskSubmissionPort | None = None,
    limits: ModelAnalysisLimits | None = None,
    database_path: str | Path | None = None,
) -> ModelAnalysisJobService:
    if repository is None:
        from agent.config import settings
        from agent.repositories.model_analysis_job_repository import (
            SQLiteModelAnalysisJobRepository,
        )

        path = (
            Path(database_path)
            if database_path is not None
            else Path(settings.data_dir) / "model-analysis-jobs.sqlite3"
        )
        repository = SQLiteModelAnalysisJobRepository(path)
    return ModelAnalysisJobService(
        repository=repository,
        tasks=tasks or HubModelAnalysisTaskSubmissionPort(),
        limits=limits,
    )


__all__ = [
    "InMemoryModelAnalysisJobRepository",
    "ModelAnalysisJobRecord",
    "ModelAnalysisJobPage",
    "ModelAnalysisJobRepository",
    "ModelAnalysisJobService",
    "ModelAnalysisJobServiceError",
    "ModelAnalysisJobState",
    "ModelAnalysisLimits",
    "ModelAnalysisRecoverySummary",
    "TERMINAL_STATES",
    "build_model_analysis_job_service",
]
