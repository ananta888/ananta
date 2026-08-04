"""Persistent Hub-owned orchestration for delegated knowledge-index jobs."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol

from agent.common.errors import TransientError
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)
from ananta_contracts.knowledge_index_execution import (
    KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS,
    KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON,
    KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
    MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES,
)

KNOWLEDGE_INDEX_JOB_SCHEMA = "ananta.knowledge_index_job.v1"
KNOWLEDGE_INDEX_RESULT_SCHEMA = "ananta.knowledge_index_job_result.v1"
KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA = (
    "ananta.knowledge_index_execution_job.v2"
)
KNOWLEDGE_INDEX_EXECUTION_RESULT_SCHEMA = (
    "ananta.knowledge_index_execution_result.v2"
)
_INLINE_JOB_PAYLOAD_BYTES = 128 * 1024
_MAX_JOB_PAYLOAD_BYTES = MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES
_PAYLOAD_MEDIA_TYPE = "application/vnd.ananta.knowledge-index-job+json"
_GRAPH_VISUAL_OPTIONS_SCHEMA = "codecompass_graph_visual_options.v1"
_EXPIRED_DISPATCH_RECONCILIATION_SCHEMA = (
    "ananta.knowledge_index_dispatch_reconciliation.v1"
)
_RECONCILABLE_TASK_STATUSES = {
    "created",
    "todo",
    "blocked",
    "blocked_by_dependency",
    "proposing",
    "assigned",
    "in_progress",
    "running",
}
_MAX_GRAPH_BLAST_RADIUS_SEEDS = 256
_MAX_GRAPH_SEED_ID_LENGTH = 512
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_WORKER_RESULT_FIELDS = frozenset(
    {
        "schema",
        "job_id",
        "idempotency_fingerprint",
        "status",
        "reason_code",
        "knowledge_index",
        "run",
        "results",
        "artifact_refs",
        "error",
    }
)
_WORKER_ARTIFACT_REQUIRED_FIELDS = frozenset({"artifact_id", "sha256", "media_type"})
_WORKER_ARTIFACT_OUTPUT_FIELDS = frozenset(
    {"role", "filename", "size_bytes", "knowledge_index_id", "run_id"}
)
_WORKER_GRAPH_ARTIFACT_FIELDS = frozenset(
    {"artifact_schema", "graph_revision", "graph_content_hash"}
)
_WORKER_ARTIFACT_FILENAMES = {
    "manifest": "manifest.json",
    "index": "index.jsonl",
    "details": "details.jsonl",
    "relations": "relations.jsonl",
    "graph_index": "cc_graph_index.json",
    "graph_visual_metrics": "cc_graph_index.visual_metrics.json",
}
_WORKER_GRAPH_ARTIFACT_SCHEMAS = {
    "graph_index": "codecompass_graph_index.v1",
    "graph_visual_metrics": "graph_visual_metrics.v1",
}


class KnowledgeIndexCompletionProjectionPending(TransientError):
    """A durable result awaits its idempotent Source-Control projection."""

    reason_code = "knowledge_index_source_projection_pending"

    def __init__(self, cause: Exception) -> None:
        projection_reason = str(
            getattr(cause, "reason_code", None)
            or type(cause).__name__
        )
        super().__init__(
            self.reason_code,
            details={"projection_reason_code": projection_reason},
        )


class KnowledgeIndexJobRepositoryPort(Protocol):
    def get_by_id(self, task_id: str) -> Any | None: ...

    def save(self, task: Any) -> Any: ...

    def replace_bound_knowledge_index_envelope(
        self,
        task_id: str,
        *,
        expected_envelope: dict,
        replacement_envelope: dict,
    ) -> Any: ...

    def compare_and_set_status(
        self,
        task_id: str,
        **options: Any,
    ) -> Any: ...


class KnowledgeIndexTaskQueuePort(Protocol):
    def ingest_task(self, **kwargs: Any) -> None: ...


class KnowledgeIndexWorkerDirectoryPort(Protocol):
    def resolve_worker_url(self, worker_id: str) -> str: ...


class KnowledgeIndexPayloadStorePort(Protocol):
    def prepare_reference(
        self,
        *,
        content: bytes,
        fingerprint: str,
    ) -> dict[str, object]: ...

    def store_payload(
        self,
        *,
        content: bytes,
        fingerprint: str,
        created_by: str | None,
    ) -> Mapping[str, Any]: ...


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _normalize_graph_visual_metrics_options(
    raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize the Hub-owned intent without importing worker algorithms."""

    if raw is not None and not isinstance(raw, Mapping):
        raise ValueError("graph_visual_options_invalid")
    options = dict(raw or {})
    allowed = {"schema", "include_advanced_metrics", "blast_radius_seeds"}
    if set(options) - allowed:
        raise ValueError("graph_visual_options_fields_unknown")
    schema = str(options.get("schema") or _GRAPH_VISUAL_OPTIONS_SCHEMA)
    if schema != _GRAPH_VISUAL_OPTIONS_SCHEMA:
        raise ValueError("graph_visual_options_schema_invalid")
    include_advanced = options.get("include_advanced_metrics", True)
    if not isinstance(include_advanced, bool):
        raise ValueError("graph_visual_options_advanced_metrics_invalid")
    raw_seeds = options.get("blast_radius_seeds", [])
    if not isinstance(raw_seeds, list) or len(raw_seeds) > _MAX_GRAPH_BLAST_RADIUS_SEEDS:
        raise ValueError("graph_visual_options_blast_seeds_invalid")
    seeds: set[str] = set()
    for raw_seed in raw_seeds:
        if not isinstance(raw_seed, str):
            raise ValueError("graph_visual_options_blast_seed_invalid")
        seed = raw_seed.strip()
        if not seed or len(seed) > _MAX_GRAPH_SEED_ID_LENGTH:
            raise ValueError("graph_visual_options_blast_seed_invalid")
        seeds.add(seed)
    return {
        "schema": _GRAPH_VISUAL_OPTIONS_SCHEMA,
        "include_advanced_metrics": include_advanced,
        "blast_radius_seeds": sorted(seeds),
    }


def _is_prefixed_sha256(value: str) -> bool:
    return (
        value.startswith("sha256:")
        and len(value) == 71
        and all(char in "0123456789abcdef" for char in value[7:])
    )


def _validate_worker_graph_artifact_reference(
    reference: Mapping[str, Any],
    *,
    role: str,
) -> None:
    if not _WORKER_GRAPH_ARTIFACT_FIELDS.issubset(reference):
        raise ValueError("knowledge_index_result_graph_artifact_ref_incomplete")
    if str(reference.get("artifact_schema") or "") != _WORKER_GRAPH_ARTIFACT_SCHEMAS[role]:
        raise ValueError("knowledge_index_result_graph_artifact_schema_invalid")
    revision = str(reference.get("graph_revision") or "")
    content_hash = str(reference.get("graph_content_hash") or "")
    if not _is_prefixed_sha256(revision):
        raise ValueError("knowledge_index_result_graph_revision_invalid")
    if not _is_prefixed_sha256(content_hash):
        raise ValueError("knowledge_index_result_graph_content_hash_invalid")


def _validate_worker_artifact_reference(reference: Any) -> None:
    if not isinstance(reference, Mapping):
        raise ValueError("knowledge_index_result_artifact_ref_invalid")
    allowed_fields = (
        _WORKER_ARTIFACT_REQUIRED_FIELDS
        | _WORKER_ARTIFACT_OUTPUT_FIELDS
        | _WORKER_GRAPH_ARTIFACT_FIELDS
    )
    if (
        not _WORKER_ARTIFACT_REQUIRED_FIELDS.issubset(reference)
        or set(reference) - allowed_fields
    ):
        raise ValueError("knowledge_index_result_artifact_ref_invalid")
    artifact_id = str(reference.get("artifact_id") or "").strip()
    digest = str(reference.get("sha256") or "")
    media_type = str(reference.get("media_type") or "").strip()
    if not artifact_id or not media_type:
        raise ValueError("knowledge_index_result_artifact_ref_invalid")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("knowledge_index_result_artifact_ref_digest_invalid")

    has_output_metadata = any(field in reference for field in _WORKER_ARTIFACT_OUTPUT_FIELDS)
    has_graph_metadata = any(field in reference for field in _WORKER_GRAPH_ARTIFACT_FIELDS)
    if not has_output_metadata:
        if has_graph_metadata:
            raise ValueError("knowledge_index_result_graph_artifact_ref_incomplete")
        return
    if not _WORKER_ARTIFACT_OUTPUT_FIELDS.issubset(reference):
        raise ValueError("knowledge_index_result_artifact_ref_incomplete")
    role = str(reference.get("role") or "")
    if role not in _WORKER_ARTIFACT_FILENAMES:
        raise ValueError("knowledge_index_result_artifact_ref_role_invalid")
    if str(reference.get("filename") or "") != _WORKER_ARTIFACT_FILENAMES[role]:
        raise ValueError("knowledge_index_result_artifact_ref_filename_invalid")
    size_bytes = reference.get("size_bytes")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
        or size_bytes > 128 * 1024 * 1024
    ):
        raise ValueError("knowledge_index_result_artifact_ref_size_invalid")
    if not str(reference.get("knowledge_index_id") or "").strip():
        raise ValueError("knowledge_index_result_artifact_ref_index_id_invalid")
    if not str(reference.get("run_id") or "").strip():
        raise ValueError("knowledge_index_result_artifact_ref_run_id_invalid")
    if role in _WORKER_GRAPH_ARTIFACT_SCHEMAS:
        if size_bytes > MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES:
            raise ValueError("knowledge_index_result_graph_artifact_ref_size_invalid")
        _validate_worker_graph_artifact_reference(reference, role=role)
    elif has_graph_metadata:
        raise ValueError("knowledge_index_result_graph_artifact_ref_unexpected")


class KnowledgeIndexJobService:
    """Persist indexing intent in the one Hub queue; never execute in the Hub.

    The service is intentionally orchestration-only.  A worker receives the
    ``knowledge_index_job`` envelope from ``worker_execution_context`` and returns
    ``ananta.knowledge_index_job_result.v1`` through the existing task result path.
    """

    def __init__(
        self,
        index_service: Any | None = None,
        *,
        task_queue: KnowledgeIndexTaskQueuePort | None = None,
        task_repository: KnowledgeIndexJobRepositoryPort | None = None,
        payload_store: KnowledgeIndexPayloadStorePort | None = None,
        worker_artifact_service: Any | None = None,
        source_control_completion_projector: Any | None = None,
        execution_binding_service: Any | None = None,
        destination_resolution_service: Any | None = None,
        worker_directory: KnowledgeIndexWorkerDirectoryPort | None = None,
        source_access_enforcement_service: Any | None = None,
        task_result_publisher: Any | None = None,
        allow_legacy_unresolved_destination: bool = False,
        allow_legacy_unsigned_source_dispatch: bool = False,
        clock=time.time,
        max_workers: int | None = None,
    ) -> None:
        # ``index_service``/``max_workers`` remain accepted so old composition code
        # fails safely instead of starting a hidden executor after an upgrade.
        del index_service, max_workers
        self._task_queue = task_queue
        self._task_repository = task_repository
        self._payload_store = payload_store
        self._worker_artifact_service = worker_artifact_service
        self._source_control_completion_projector = (
            source_control_completion_projector
        )
        self._execution_binding_service = execution_binding_service
        self._destination_resolution_service = (
            destination_resolution_service
        )
        self._worker_directory = worker_directory
        self._source_access_enforcement_service = (
            source_access_enforcement_service
        )
        self._task_result_publisher = task_result_publisher
        self._allow_legacy_unresolved_destination = bool(
            allow_legacy_unresolved_destination
        )
        self._allow_legacy_unsigned_source_dispatch = bool(
            allow_legacy_unsigned_source_dispatch
        )
        self._clock = clock

    def _queue(self) -> KnowledgeIndexTaskQueuePort:
        if self._task_queue is not None:
            return self._task_queue
        from agent.services.task_queue_service import get_task_queue_service

        return get_task_queue_service()

    def _repository(self) -> KnowledgeIndexJobRepositoryPort:
        if self._task_repository is not None:
            return self._task_repository
        from agent.repository import task_repo

        return task_repo

    def _result_publisher(self) -> Any:
        if self._task_result_publisher is not None:
            return self._task_result_publisher
        from agent.services.knowledge_index_task_result_publication import (
            KnowledgeIndexTaskResultPublisher,
        )
        from agent.services.task_runtime_service import (
            run_external_task_status_post_commit,
        )

        return KnowledgeIndexTaskResultPublisher(
            repository=self._repository(),
            execution_binding_service=self._execution_binding_service,
            post_commit=run_external_task_status_post_commit,
        )

    def _persist_bound_execution_envelope(
        self,
        *,
        job_id: str,
        expected_envelope: Mapping[str, Any],
        envelope: Mapping[str, Any],
    ) -> None:
        repository = self._repository()
        atomic_replace = getattr(
            repository,
            "replace_bound_knowledge_index_envelope",
            None,
        )
        if not callable(atomic_replace):
            raise RuntimeError(
                "knowledge_index_atomic_task_envelope_repository_required"
            )
        atomic_replace(
            str(job_id),
            expected_envelope=dict(expected_envelope),
            replacement_envelope=dict(envelope),
        )

    def authorize_bound_worker_dispatch(
        self,
        *,
        job_id: str,
        authenticated_worker_id: str,
        destination_selection: Mapping[str, Any] | None = None,
        dispatch_phase: str = "execute",
    ) -> dict[str, Any]:
        """Return v2 execution context only after the mandatory Hub gate."""

        normalized_dispatch_phase = str(dispatch_phase or "").strip().lower()
        if normalized_dispatch_phase not in {"propose", "execute"}:
            raise ValueError("knowledge_index_dispatch_phase_invalid")

        task = self._repository().get_by_id(str(job_id))
        if task is None:
            raise ValueError("knowledge_index_job_not_found")
        raw_task = (
            task.model_dump() if hasattr(task, "model_dump") else dict(task)
        )
        context = dict(raw_task.get("worker_execution_context") or {})
        envelope = dict(context.get("knowledge_index_job") or {})
        if (
            str(envelope.get("schema") or "")
            != KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
        ):
            raise ValueError("knowledge_index_execution_job_schema_invalid")
        service = self._execution_binding_service
        if service is None:
            raise RuntimeError(
                "knowledge_index_execution_binding_service_unavailable"
            )
        authorized = service.validate_before_dispatch(
            job_id=str(job_id),
            authenticated_worker_id=str(authenticated_worker_id),
        )
        current_envelope = authorized.job.to_wire()
        persisted_enforcement_manifest = envelope.pop(
            "source_access_enforcement_manifest",
            None,
        )
        if envelope != current_envelope:
            raise ValueError(
                "knowledge_index_execution_queue_context_stale"
            )
        authority = dict(
            current_envelope.get("authority_binding") or {}
        )
        assignment = dict(current_envelope.get("assignment") or {})
        resolver = self._destination_resolution_service
        if resolver is not None:
            if not isinstance(destination_selection, Mapping):
                raise ValueError(
                    "knowledge_index_destination_selection_required"
                )
            from agent.services.source_destination_resolution import (
                DestinationSelection,
            )

            resolved = resolver.verify_dispatch_binding(
                preview_destination_digest=str(
                    authority.get("destination_digest") or ""
                ),
                dispatch_selection=DestinationSelection(
                    **dict(destination_selection)
                ),
            )
            if (
                resolved.descriptor.destination_id
                != authority.get("destination_id")
            ):
                raise ValueError(
                    "knowledge_index_destination_id_changed"
                )
            if (
                resolved.descriptor.worker_id
                != assignment.get("worker_id")
            ):
                raise ValueError(
                    "knowledge_index_destination_assignment_mismatch"
                )
        elif not self._allow_legacy_unresolved_destination:
            raise RuntimeError(
                "knowledge_index_destination_resolution_unavailable"
            )

        if normalized_dispatch_phase == "propose":
            # Proposal generation is metadata-only.  Source grants and payload
            # capabilities are deliberately withheld until the Hub atomically
            # claims the execute phase.
            return {"knowledge_index_job": current_envelope}

        required_dispatch_window_ms = (
            self._assert_dispatch_runtime_window(current_envelope)
        )

        enforcement = self._source_access_enforcement_service
        if enforcement is None:
            if persisted_enforcement_manifest is not None:
                raise RuntimeError(
                    "knowledge_index_source_access_enforcement_unavailable"
                )
            if not self._allow_legacy_unsigned_source_dispatch:
                raise RuntimeError(
                    "knowledge_index_source_access_enforcement_unavailable"
                )
            worker_envelope = current_envelope
            self._claim_execution_dispatch(
                service=service,
                authorized_record=authorized,
                job_id=str(job_id),
                authenticated_worker_id=str(authenticated_worker_id),
                dispatch_phase=normalized_dispatch_phase,
            )
            return {"knowledge_index_job": worker_envelope}

        from dataclasses import asdict

        from agent.services.source_access_enforcement import (
            SourceAccessRequest,
        )
        from ananta_contracts.source_control import (
            GrantOperation,
            GrantTransformation,
        )

        intent = dict(context.get("source_access_intent") or {})
        if str(intent.get("policy_version") or "") != str(
            authority.get("policy_snapshot_id") or ""
        ):
            raise ValueError(
                "knowledge_index_source_policy_binding_mismatch"
            )
        manifest = dict(current_envelope.get("file_manifest") or {})
        request = SourceAccessRequest(
            tenant_id=str(authority.get("tenant_id") or ""),
            project_id=str(authority.get("project_id") or ""),
            source_revision_id=str(
                authority.get("source_revision_id") or ""
            ),
            source_revision_digest=str(
                authority.get("source_revision_digest") or ""
            ),
            destination_id=str(
                authority.get("destination_id") or ""
            ),
            destination_digest=str(
                authority.get("destination_digest") or ""
            ),
            source_access_grant_id=str(
                authority.get("source_access_grant_id") or ""
            ),
            source_access_grant_digest=str(
                authority.get("source_access_grant_digest") or ""
            ),
            operation=GrantOperation(
                str(intent.get("operation") or "")
            ),
            transformation=GrantTransformation(
                str(intent.get("transformation") or "")
            ),
            purpose=str(intent.get("purpose") or ""),
            policy_version=str(intent.get("policy_version") or ""),
            policy_digest=str(
                authority.get("policy_snapshot_digest") or ""
            ),
            manifest_id=str(manifest.get("manifest_id") or ""),
            manifest_digest=str(
                manifest.get("manifest_digest") or ""
            ),
            assignment_id=str(
                assignment.get("assignment_id") or ""
            ),
            lease_id=str(assignment.get("lease_id") or ""),
        )
        dispatch_time = datetime.fromtimestamp(
            float(self._clock()),
            tz=timezone.utc,
        )
        if persisted_enforcement_manifest is not None:
            if not isinstance(persisted_enforcement_manifest, Mapping):
                raise ValueError(
                    "knowledge_index_source_access_manifest_invalid"
                )
            verified_manifest = enforcement.validate_delegated_manifest(
                persisted_enforcement_manifest,
                request,
                now=dispatch_time,
                minimum_remaining_ms=required_dispatch_window_ms,
            )
            worker_envelope = {
                **current_envelope,
                "source_access_enforcement_manifest": asdict(
                    verified_manifest
                ),
            }
            self._claim_execution_dispatch(
                service=service,
                authorized_record=authorized,
                job_id=str(job_id),
                authenticated_worker_id=str(authenticated_worker_id),
                dispatch_phase=normalized_dispatch_phase,
            )
            return {"knowledge_index_job": worker_envelope}
        source_dispatch = enforcement.authorize(
            request,
            now=dispatch_time,
            allow_exact_consumption_recovery=True,
            minimum_remaining_ms=required_dispatch_window_ms,
        )
        worker_envelope = {
            **current_envelope,
            "source_access_enforcement_manifest": asdict(
                source_dispatch.manifest
            ),
        }
        self._persist_bound_execution_envelope(
            job_id=job_id,
            expected_envelope=current_envelope,
            envelope=worker_envelope,
        )
        self._claim_execution_dispatch(
            service=service,
            authorized_record=authorized,
            job_id=str(job_id),
            authenticated_worker_id=str(authenticated_worker_id),
            dispatch_phase=normalized_dispatch_phase,
        )
        return {"knowledge_index_job": worker_envelope}

    def _assert_dispatch_runtime_window(
        self,
        envelope: Mapping[str, Any],
    ) -> int:
        """Reject execute dispatch before consuming its one-time authority."""

        assignment = envelope.get("assignment")
        resources = envelope.get("resources")
        if not isinstance(assignment, Mapping) or not isinstance(
            resources,
            Mapping,
        ):
            raise ValueError(
                KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON
            )
        lease_expires_epoch_ms = assignment.get(
            "lease_expires_epoch_ms"
        )
        max_runtime_seconds = resources.get("max_runtime_seconds")
        if (
            isinstance(lease_expires_epoch_ms, bool)
            or not isinstance(lease_expires_epoch_ms, int)
            or isinstance(max_runtime_seconds, bool)
            or not isinstance(max_runtime_seconds, int)
            or max_runtime_seconds < 1
        ):
            raise ValueError(
                KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON
            )
        try:
            now_epoch_ms = int(float(self._clock()) * 1000)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON
            ) from exc
        required_window_ms = (
            max_runtime_seconds
            + KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS
        ) * 1000
        if lease_expires_epoch_ms - now_epoch_ms < required_window_ms:
            raise ValueError(
                KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON
            )
        return required_window_ms

    @staticmethod
    def _claim_execution_dispatch(
        *,
        service: Any,
        authorized_record: Any,
        job_id: str,
        authenticated_worker_id: str,
        dispatch_phase: str,
    ) -> None:
        if dispatch_phase != "execute":
            return
        claim = getattr(service, "claim_dispatch", None)
        if not callable(claim):
            raise RuntimeError(
                "knowledge_index_dispatch_claim_service_unavailable"
            )
        claim(
            job_id=job_id,
            authenticated_worker_id=authenticated_worker_id,
            expected_lock_version=int(authorized_record.lock_version),
        )

    def retry_bound_job(
        self,
        *,
        job_id: str,
        assignment: Mapping[str, Any],
        **retry_options: Any,
    ) -> dict[str, Any]:
        """Retry through the Hub gate and fail closed on stale queue context."""

        from ananta_contracts.knowledge_index_execution import (
            KnowledgeIndexExecutionAssignment,
        )

        service = self._execution_binding_service
        if service is None:
            raise RuntimeError(
                "knowledge_index_execution_binding_service_unavailable"
            )
        task = self._repository().get_by_id(str(job_id))
        if task is None:
            raise ValueError("knowledge_index_job_not_found")
        raw_task = (
            task.model_dump()
            if hasattr(task, "model_dump")
            else dict(task)
        )
        expected_envelope = dict(
            dict(raw_task.get("worker_execution_context") or {}).get(
                "knowledge_index_job"
            )
            or {}
        )
        record = service.retry(
            job_id=str(job_id),
            assignment=KnowledgeIndexExecutionAssignment.model_validate(
                dict(assignment)
            ),
            **retry_options,
        )
        self._persist_bound_execution_envelope(
            job_id=str(job_id),
            expected_envelope=expected_envelope,
            envelope=record.job.to_wire(),
        )
        return self.get_job(str(job_id)) or {
            "job_id": str(job_id),
            "status": record.state,
        }

    def reconcile_expired_bound_dispatch(
        self,
        *,
        job_id: str,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        """Project one expired Hub assignment or dispatch without replay."""

        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise ValueError("knowledge_index_job_not_found")
        try:
            normalized_lock_version = int(expected_lock_version)
        except (TypeError, ValueError):
            normalized_lock_version = 0
        if isinstance(expected_lock_version, bool) or normalized_lock_version < 1:
            raise ValueError("knowledge_index_execution_lock_version_invalid")
        binding_service = self._execution_binding_service
        if binding_service is None:
            raise RuntimeError(
                "knowledge_index_execution_binding_service_unavailable"
            )
        reconcile = getattr(
            binding_service,
            "reconcile_expired_dispatch",
            None,
        )
        if not callable(reconcile):
            raise RuntimeError(
                "knowledge_index_execution_reconcile_service_unavailable"
            )
        record = reconcile(
            job_id=normalized_job_id,
            expected_lock_version=normalized_lock_version,
        )
        if record.state != "failed" or record.completed_at_epoch_ms is None:
            raise RuntimeError(
                "knowledge_index_execution_reconcile_result_invalid"
            )

        repository = self._repository()
        status_cas = getattr(repository, "compare_and_set_status", None)
        if not callable(status_cas):
            raise RuntimeError(
                "knowledge_index_atomic_task_status_repository_required"
            )
        marker = self._expired_dispatch_reconciliation_marker(record)
        result = status_cas(
            normalized_job_id,
            expected_statuses=set(_RECONCILABLE_TASK_STATUSES),
            target_status="failed",
            predicate=lambda task: self._task_matches_bound_execution(
                task,
                expected_envelope=record.job.to_wire(),
            ),
            mutate=lambda task: self._project_expired_dispatch_failure(
                task,
                marker=marker,
            ),
        )
        projected_task = getattr(result, "task", None)
        if not bool(getattr(result, "updated", False)) and not (
            projected_task is not None
            and self._task_has_expired_dispatch_projection(
                projected_task,
                marker=marker,
            )
        ):
            raise ValueError(
                "knowledge_index_execution_task_projection_conflict"
            )
        return {
            "job_id": normalized_job_id,
            "status": "failed",
            "reason_code": KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
            "execution_state": record.state,
            "execution_lock_version": int(record.lock_version),
            "completed_at_epoch_ms": int(record.completed_at_epoch_ms),
        }

    @staticmethod
    def _task_mapping(task: Any) -> dict[str, Any]:
        if hasattr(task, "model_dump"):
            return dict(task.model_dump())
        if isinstance(task, Mapping):
            return dict(task)
        return dict(vars(task))

    @classmethod
    def _task_matches_bound_execution(
        cls,
        task: Any,
        *,
        expected_envelope: Mapping[str, Any],
    ) -> bool:
        raw = cls._task_mapping(task)
        envelope = dict(
            dict(raw.get("worker_execution_context") or {}).get(
                "knowledge_index_job"
            )
            or {}
        )
        envelope.pop("source_access_enforcement_manifest", None)
        return envelope == dict(expected_envelope)

    @staticmethod
    def _expired_dispatch_reconciliation_marker(record: Any) -> dict[str, Any]:
        assignment = record.job.assignment
        return {
            "schema": _EXPIRED_DISPATCH_RECONCILIATION_SCHEMA,
            "job_id": record.job.job_id,
            "reason_code": KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
            "authority_binding_digest": (
                record.job.authority_binding.binding_digest
            ),
            "assignment_id": assignment.assignment_id,
            "lease_id": assignment.lease_id,
            "lease_generation": assignment.lease_generation,
            "lease_expires_epoch_ms": assignment.lease_expires_epoch_ms,
            "execution_lock_version": int(record.lock_version),
            "reconciled_at_epoch_ms": int(record.completed_at_epoch_ms),
        }

    @classmethod
    def _project_expired_dispatch_failure(
        cls,
        task: Any,
        *,
        marker: Mapping[str, Any],
    ) -> None:
        raw = cls._task_mapping(task)
        details = dict(raw.get("status_reason_details") or {})
        details["knowledge_index_dispatch_reconciliation"] = dict(marker)
        if isinstance(task, dict):
            task["status_reason_code"] = (
                KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON
            )
            task["status_reason_details"] = details
            return
        setattr(
            task,
            "status_reason_code",
            KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
        )
        setattr(task, "status_reason_details", details)

    @classmethod
    def _task_has_expired_dispatch_projection(
        cls,
        task: Any,
        *,
        marker: Mapping[str, Any],
    ) -> bool:
        raw = cls._task_mapping(task)
        details = dict(raw.get("status_reason_details") or {})
        return bool(
            str(raw.get("status") or "").strip().lower() == "failed"
            and str(raw.get("status_reason_code") or "")
            == KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON
            and details.get("knowledge_index_dispatch_reconciliation")
            == dict(marker)
        )

    def _store_large_payload(
        self,
        *,
        content: bytes,
        fingerprint: str,
        created_by: str | None,
    ) -> dict[str, Any]:
        if self._payload_store is not None:
            raw_reference = self._payload_store.store_payload(
                content=content,
                fingerprint=fingerprint,
                created_by=created_by,
            )
        else:
            from agent.services.ingestion_service import get_ingestion_service

            artifact, version, _collection = get_ingestion_service().upload_artifact(
                filename=f"knowledge-index-payload-{fingerprint}.json",
                content=content,
                created_by=created_by or "knowledge-index-api",
                media_type=_PAYLOAD_MEDIA_TYPE,
                artifact_metadata={
                    "system_artifact_kind": (
                        "knowledge_index_job_payload"
                    ),
                    "idempotency_fingerprint": fingerprint,
                },
            )
            from agent.repository import artifact_repo

            artifact.artifact_metadata = {
                **dict(artifact.artifact_metadata or {}),
                "system_artifact_kind": "knowledge_index_job_payload",
                "idempotency_fingerprint": fingerprint,
            }
            artifact_repo.save(artifact)
            raw_reference = {
                "artifact_id": artifact.id,
                "sha256": version.sha256,
                "size_bytes": version.size_bytes,
                "media_type": version.media_type,
            }
        return self._normalize_payload_reference(
            raw_reference,
            content=content,
        )

    @staticmethod
    def _normalize_payload_reference(
        raw_reference: Mapping[str, Any] | None,
        *,
        content: bytes,
    ) -> dict[str, Any]:
        reference = dict(raw_reference or {})
        artifact_id = str(reference.get("artifact_id") or "").strip()
        digest = str(reference.get("sha256") or "").strip().lower()
        media_type = str(reference.get("media_type") or "").strip().lower()
        size_bytes = int(reference.get("size_bytes") or -1)
        if not artifact_id:
            raise RuntimeError("knowledge_index_payload_artifact_id_missing")
        if digest != hashlib.sha256(content).hexdigest():
            raise RuntimeError("knowledge_index_payload_artifact_digest_mismatch")
        if size_bytes != len(content):
            raise RuntimeError("knowledge_index_payload_artifact_size_mismatch")
        if media_type != _PAYLOAD_MEDIA_TYPE:
            raise RuntimeError("knowledge_index_payload_artifact_media_type_mismatch")
        return {
            "artifact_id": artifact_id,
            "sha256": digest,
            "size_bytes": size_bytes,
            "media_type": media_type,
            "encoding": "json",
        }

    def _prepare_large_payload_reference(
        self,
        *,
        content: bytes,
        fingerprint: str,
    ) -> dict[str, Any]:
        payload_store = self._payload_store
        prepare_reference = getattr(
            payload_store,
            "prepare_reference",
            None,
        )
        if not callable(prepare_reference):
            raise RuntimeError(
                "knowledge_index_content_addressed_payload_store_required"
            )
        raw_reference = prepare_reference(
            content=content,
            fingerprint=fingerprint,
        )
        return self._normalize_payload_reference(
            raw_reference,
            content=content,
        )

    def _store_prepared_payload(
        self,
        *,
        content: bytes,
        fingerprint: str,
        created_by: str,
        expected_reference: Mapping[str, Any],
    ) -> None:
        stored_reference = self._store_large_payload(
            content=content,
            fingerprint=fingerprint,
            created_by=created_by,
        )
        if stored_reference != dict(expected_reference):
            raise RuntimeError(
                "knowledge_index_payload_artifact_reference_changed"
            )

    def _ensure_bound_task_projection(
        self,
        *,
        record: Any,
        assigned_worker_url: str,
        destination_selection: Mapping[str, Any],
        source_access_intent: Mapping[str, Any],
    ) -> dict[str, Any]:
        envelope = record.job.to_wire()
        task = self._repository().get_by_id(record.job.job_id)
        if task is None:
            try:
                self._queue().ingest_task(
                    task_id=record.job.job_id,
                    status="assigned",
                    title=(
                        f"Knowledge index revision: {record.job.scope_id}"
                    )[:200],
                    description=(
                        "Hub-authorized immutable source-revision index job."
                    ),
                    priority="medium",
                    created_by=record.job.created_by,
                    source="knowledge_index",
                    team_id=record.job.authority_binding.project_id,
                    tags=[
                        "knowledge_index",
                        "hub_delegated",
                        "source_revision_bound",
                    ],
                    event_type="task_ingested",
                    event_channel="hub_task_queue",
                    event_details={
                        "domain_event_type": (
                            "knowledge_index_execution_authorized"
                        ),
                        "authority_binding_digest": envelope[
                            "authority_binding"
                        ]["binding_digest"],
                    },
                    extra_fields={
                        "task_kind": "codecompass_index_build",
                        "retrieval_intent": "index_snapshot",
                        "required_context_scope": record.job.source_scope,
                        "required_capabilities": [
                            "retrieval",
                            "index_write",
                        ],
                        "assigned_agent_url": assigned_worker_url,
                        "worker_execution_context": {
                            "knowledge_index_job": envelope,
                            "destination_selection": dict(
                                destination_selection
                            ),
                            "source_access_intent": dict(
                                source_access_intent
                            ),
                        },
                        "verification_spec": {
                            "schema": (
                                KNOWLEDGE_INDEX_EXECUTION_RESULT_SCHEMA
                            ),
                            "artifact_first": True,
                            "authority_binding_digest": envelope[
                                "authority_binding"
                            ]["binding_digest"],
                            "file_manifest_digest": envelope[
                                "file_manifest"
                            ]["manifest_digest"],
                        },
                    },
                )
            except Exception:
                # Some queue adapters can fail after their durable write. A
                # matching projection means the submission still succeeded.
                task = self._repository().get_by_id(record.job.job_id)
                if task is None:
                    raise
            else:
                task = self._repository().get_by_id(record.job.job_id)
        if task is None:
            raise RuntimeError(
                "knowledge_index_execution_queue_projection_missing"
            )
        self._validate_bound_task_projection(
            task,
            record=record,
            assigned_worker_url=assigned_worker_url,
            destination_selection=destination_selection,
            source_access_intent=source_access_intent,
        )
        projected = self.get_job(record.job.job_id)
        if projected is None:
            raise RuntimeError(
                "knowledge_index_execution_queue_projection_invalid"
            )
        return projected

    @classmethod
    def _validate_bound_task_projection(
        cls,
        task: Any,
        *,
        record: Any,
        assigned_worker_url: str,
        destination_selection: Mapping[str, Any],
        source_access_intent: Mapping[str, Any],
    ) -> None:
        raw = cls._task_mapping(task)
        context = dict(raw.get("worker_execution_context") or {})
        projected_envelope = dict(
            context.get("knowledge_index_job") or {}
        )
        projected_envelope.pop(
            "source_access_enforcement_manifest",
            None,
        )
        expected_worker_url = str(assigned_worker_url).rstrip("/")
        projected_worker_url = str(
            raw.get("assigned_agent_url") or ""
        ).rstrip("/")
        if (
            str(raw.get("task_kind") or "")
            != "codecompass_index_build"
            or projected_worker_url != expected_worker_url
            or projected_envelope != record.job.to_wire()
            or dict(context.get("destination_selection") or {})
            != dict(destination_selection)
            or dict(context.get("source_access_intent") or {})
            != dict(source_access_intent)
        ):
            raise RuntimeError(
                "knowledge_index_execution_queue_projection_conflict"
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        task = self._repository().get_by_id(str(job_id))
        if task is None:
            return None
        raw = task.model_dump() if hasattr(task, "model_dump") else dict(task)
        context = dict(raw.get("worker_execution_context") or {})
        envelope = dict(context.get("knowledge_index_job") or {})
        envelope_schema = str(envelope.get("schema") or "")
        if envelope_schema not in {
            KNOWLEDGE_INDEX_JOB_SCHEMA,
            KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
        }:
            return None
        task_status = str(raw.get("status") or "todo").strip().lower()
        # Proposal is still queue-facing work. The independent execution
        # projection below remains authoritative when its binding is already
        # running, so callers can observe both facts without a read-side write.
        status = {
            "created": "queued",
            "todo": "queued",
            "blocked": "queued",
            "blocked_by_dependency": "queued",
            "proposing": "queued",
            "assigned": "running",
            "in_progress": "running",
            "running": "running",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(task_status, "queued")
        verification = dict(raw.get("verification_status") or {})
        result = verification.get("knowledge_index_job_result")
        payload = {
            "job_id": str(raw.get("id") or envelope.get("job_id") or ""),
            "job_type": envelope.get("job_type"),
            "scope_id": envelope.get("scope_id"),
            "source_scope": envelope.get("source_scope"),
            "status": status,
            "phase": "completed" if status == "completed" else "failed" if status == "failed" else status,
            "progress_percent": 100 if status in _TERMINAL_STATUSES else 10 if status == "running" else 0,
            "created_by": envelope.get("created_by"),
            "profile_name": envelope.get("profile_name"),
            "created_at": raw.get("created_at", envelope.get("created_at")),
            "updated_at": raw.get("updated_at"),
            "record_count": envelope.get("record_count"),
            "artifact_ids": envelope.get("artifact_ids"),
            "idempotency_fingerprint": envelope.get("idempotency_fingerprint"),
            "task_kind": raw.get("task_kind"),
            "reason_code": raw.get("status_reason_code"),
            "tenant_id": (envelope.get("authority_binding") or {}).get(
                "tenant_id"
            ),
            "project_id": (envelope.get("authority_binding") or {}).get(
                "project_id"
            ),
            "source_revision_id": (
                envelope.get("authority_binding") or {}
            ).get("source_revision_id"),
            "file_manifest_digest": (
                envelope.get("file_manifest") or {}
            ).get("manifest_digest"),
            "assignment_id": (envelope.get("assignment") or {}).get(
                "assignment_id"
            ),
        }
        if (
            envelope_schema == KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
            and self._execution_binding_service is not None
        ):
            get_record = getattr(
                self._execution_binding_service,
                "get_record",
                None,
            )
            if callable(get_record):
                try:
                    execution_record = get_record(str(job_id))
                except Exception as exc:
                    if getattr(exc, "reason_code", None) != (
                        "knowledge_index_execution_not_found"
                    ):
                        raise
                else:
                    payload.update(
                        {
                            "execution_state": execution_record.state,
                            "execution_lock_version": int(
                                execution_record.lock_version
                            ),
                            "lease_expires_epoch_ms": int(
                                execution_record.job.assignment
                                .lease_expires_epoch_ms
                            ),
                        }
                    )
            get_projection = getattr(
                self._execution_binding_service,
                "get_completion_projection",
                None,
            )
            if callable(get_projection):
                try:
                    completion_projection = get_projection(
                        str(job_id),
                        require_terminal_result=False,
                    )
                except Exception as exc:
                    if getattr(exc, "reason_code", None) not in {
                        "knowledge_index_completion_projection_not_found",
                        "knowledge_index_execution_not_found",
                    }:
                        raise
                else:
                    payload.update(
                        {
                            "completion_projection_state": (
                                completion_projection.state
                            ),
                            "completion_projection_lock_version": int(
                                completion_projection.lock_version
                            ),
                        }
                    )
        if isinstance(result, Mapping):
            payload["result"] = dict(result)
            payload["knowledge_index"] = result.get("knowledge_index")
            payload["run"] = result.get("run")
            payload["results"] = result.get("results")
            payload["error"] = result.get("error")
        return {key: value for key, value in payload.items() if value is not None}

    def submit_artifact_job(
        self,
        *,
        artifact_id: str,
        created_by: str | None,
        profile_name: str | None,
        profile_overrides: dict[str, Any] | None,
        graph_visual_metrics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact = str(artifact_id or "").strip()
        if not artifact:
            raise ValueError("artifact_id_required")
        return self._submit(
            job_type="artifact",
            scope_id=artifact,
            created_by=created_by,
            profile_name=profile_name,
            payload={
                "artifact_id": artifact,
                "profile_overrides": dict(profile_overrides or {}),
                "graph_visual_metrics": _normalize_graph_visual_metrics_options(
                    graph_visual_metrics
                ),
            },
        )

    def submit_collection_job(
        self,
        *,
        collection_id: str,
        artifact_ids: list[str],
        created_by: str | None,
        profile_name: str | None,
        profile_overrides: dict[str, Any] | None,
        graph_visual_metrics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        collection = str(collection_id or "").strip()
        artifacts = sorted({str(item).strip() for item in artifact_ids if str(item).strip()})
        if not collection:
            raise ValueError("collection_id_required")
        if not artifacts:
            raise ValueError("collection_artifacts_required")
        return self._submit(
            job_type="collection",
            scope_id=collection,
            created_by=created_by,
            profile_name=profile_name,
            payload={
                "collection_id": collection,
                "artifact_ids": artifacts,
                "profile_overrides": dict(profile_overrides or {}),
                "graph_visual_metrics": _normalize_graph_visual_metrics_options(
                    graph_visual_metrics
                ),
            },
        )

    def submit_source_records_job(
        self,
        *,
        source_scope: str,
        source_id: str,
        records: list[dict[str, Any]],
        created_by: str | None,
        profile_name: str | None,
        source_metadata: dict[str, Any] | None = None,
        codecompass_prerender: bool = False,
        graph_visual_metrics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        scope = str(source_scope or "").strip().lower()
        source = str(source_id or "").strip()
        normalized_records = [dict(item) for item in records if isinstance(item, dict)]
        if not scope:
            raise ValueError("source_scope_required")
        if not source:
            raise ValueError("source_id_required")
        if len(normalized_records) != len(records):
            raise ValueError("source_records_invalid")
        return self._submit(
            job_type="source_records",
            scope_id=source,
            source_scope=scope,
            created_by=created_by,
            profile_name=profile_name,
            payload={
                "source_scope": scope,
                "source_id": source,
                "records": normalized_records,
                "source_metadata": dict(source_metadata or {}),
                "codecompass_prerender": bool(codecompass_prerender),
                "graph_visual_metrics": _normalize_graph_visual_metrics_options(
                    graph_visual_metrics
                ),
            },
        )

    def submit_bound_source_revision_job(
        self,
        *,
        hub_task_id: str,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        source_revision_id: str,
        source_revision_digest: str,
        admission_digest: str,
        policy_snapshot_id: str,
        policy_snapshot_digest: str,
        destination_id: str,
        destination_digest: str,
        source_access_grant_id: str,
        source_access_grant_digest: str,
        files: list[dict[str, Any]],
        resource_budget: Mapping[str, Any],
        assignment: Mapping[str, Any],
        destination_selection: Mapping[str, Any] | None = None,
        idempotency_key: str,
        source_scope: str,
        source_id: str,
        records: list[dict[str, Any]],
        created_by: str,
        profile_name: str = "default",
        source_operation: str = "index",
        source_transformation: str = "redacted",
        source_purpose: str = "knowledge-index",
        source_policy_version: str | None = None,
    ) -> dict[str, Any]:
        from agent.services.knowledge_index_execution_binding_service import (
            CurrentKnowledgeIndexAuthority,
        )
        from ananta_contracts.knowledge_index_execution import (
            KnowledgeIndexExecutionAssignment,
            KnowledgeIndexResourceBudget,
        )

        service = self._execution_binding_service
        if service is None:
            raise RuntimeError(
                "knowledge_index_execution_binding_service_unavailable"
            )
        raw_key = str(idempotency_key or "")
        if not raw_key:
            raise ValueError("knowledge_index_idempotency_key_required")
        idempotency_key_digest = hashlib.sha256(
            raw_key.encode("utf-8")
        ).hexdigest()
        effective_source_policy_version = str(
            source_policy_version or policy_snapshot_id
        )
        if effective_source_policy_version != str(policy_snapshot_id):
            raise ValueError(
                "knowledge_index_source_policy_binding_mismatch"
            )
        from ananta_contracts.source_control import (
            GrantOperation,
            GrantTransformation,
        )

        try:
            normalized_operation = GrantOperation(
                str(source_operation)
            ).value
            normalized_transformation = GrantTransformation(
                str(source_transformation)
            ).value
        except ValueError:
            raise ValueError(
                "knowledge_index_source_access_intent_invalid"
            ) from None
        normalized_purpose = str(source_purpose or "").strip()
        if not normalized_purpose or len(normalized_purpose) > 160:
            raise ValueError(
                "knowledge_index_source_access_intent_invalid"
            )
        source_access_intent = {
            "operation": normalized_operation,
            "transformation": normalized_transformation,
            "purpose": normalized_purpose,
            "policy_version": effective_source_policy_version,
        }
        assignment_contract = (
            KnowledgeIndexExecutionAssignment.model_validate(
                dict(assignment)
            )
        )
        resource_contract = KnowledgeIndexResourceBudget.model_validate(
            dict(resource_budget)
        )
        normalized_destination_selection: dict[str, Any] = {}
        resolver = self._destination_resolution_service
        if resolver is not None:
            if not isinstance(destination_selection, Mapping):
                raise ValueError(
                    "knowledge_index_destination_selection_required"
                )
            from agent.services.source_destination_resolution import (
                DestinationSelection,
            )

            normalized_destination_selection = dict(
                destination_selection
            )
            resolved_destination = resolver.verify_dispatch_binding(
                preview_destination_digest=str(destination_digest),
                dispatch_selection=DestinationSelection(
                    **normalized_destination_selection
                ),
            )
            if resolved_destination.descriptor.destination_id != str(
                destination_id
            ):
                raise ValueError(
                    "knowledge_index_destination_id_changed"
                )
            if (
                resolved_destination.descriptor.worker_id
                != assignment_contract.worker_id
            ):
                raise ValueError(
                    "knowledge_index_destination_assignment_mismatch"
                )
        elif not self._allow_legacy_unresolved_destination:
            raise RuntimeError(
                "knowledge_index_destination_resolution_unavailable"
            )
        elif destination_selection is not None:
            if not isinstance(destination_selection, Mapping):
                raise ValueError(
                    "knowledge_index_destination_selection_invalid"
                )
            normalized_destination_selection = dict(
                destination_selection
            )
        if self._worker_directory is None:
            raise RuntimeError(
                "knowledge_index_worker_directory_unavailable"
            )
        # Resolve the immutable assignment before storing a payload or
        # admitting an execution record. A disappeared/ambiguous Worker must
        # not leave an orphaned capability or binding behind.
        assigned_worker_url = self._worker_directory.resolve_worker_url(
            assignment_contract.worker_id
        )
        if not isinstance(records, list) or any(
            not isinstance(item, Mapping) for item in records
        ):
            raise ValueError("source_records_invalid")
        normalized_records = [dict(item) for item in records]
        payload = {
            "source_scope": str(source_scope),
            "source_id": f"bound-source:{source_revision_id}",
            "records": normalized_records,
            "source_metadata": {
                "source_revision_id": source_revision_id,
                "source_revision_digest": source_revision_digest,
                "connection_source_id": str(source_id),
                # Queue-only intent participates in the immutable payload
                # identity, so replaying the same key with altered dispatch
                # semantics is rejected even after a queue-write failure.
                "dispatch_context_digest": _fingerprint(
                    {
                        "destination_selection": (
                            normalized_destination_selection
                        ),
                        "source_access_intent": source_access_intent,
                    }
                ),
            },
            "codecompass_prerender": False,
            "graph_visual_metrics": _normalize_graph_visual_metrics_options(
                None
            ),
        }
        content = _canonical_json(payload)
        if len(content) > _MAX_JOB_PAYLOAD_BYTES:
            raise ValueError("knowledge_index_job_payload_too_large")
        payload_fingerprint = hashlib.sha256(content).hexdigest()
        payload_reference = self._prepare_large_payload_reference(
            content=content,
            fingerprint=payload_fingerprint,
        )
        authority_contract = CurrentKnowledgeIndexAuthority(
            tenant_id=tenant_id,
            project_id=project_id,
            source_revision_id=source_revision_id,
            source_revision_digest=source_revision_digest,
            admission_digest=admission_digest,
            policy_snapshot_id=policy_snapshot_id,
            policy_snapshot_digest=policy_snapshot_digest,
            destination_id=destination_id,
            destination_digest=destination_digest,
            source_access_grant_id=source_access_grant_id,
            source_access_grant_digest=source_access_grant_digest,
        )
        prepared = service.prepare_issue(
            hub_task_id=hub_task_id,
            owner_id=owner_id,
            idempotency_key_digest=idempotency_key_digest,
            authority=authority_contract,
            files=files,
            resources=resource_contract,
            payload_artifact_ref=payload_reference,
            assignment=assignment_contract,
            scope_id=str(source_id),
            source_scope=str(source_scope),
            profile_name=str(profile_name),
            created_by=str(created_by),
        )
        preexisting_task = self._repository().get_by_id(
            prepared.job.job_id
        )
        if preexisting_task is not None:
            self._validate_bound_task_projection(
                preexisting_task,
                record=prepared,
                assigned_worker_url=assigned_worker_url,
                destination_selection=normalized_destination_selection,
                source_access_intent=source_access_intent,
            )
        existing = service.get_by_idempotency(
            tenant_id=tenant_id,
            project_id=project_id,
            idempotency_key_digest=idempotency_key_digest,
        )
        if existing is not None:
            if not service.same_submission(existing, prepared):
                from agent.services.knowledge_index_execution_binding_service import (
                    KnowledgeIndexExecutionBindingError,
                )

                raise KnowledgeIndexExecutionBindingError(
                    "knowledge_index_execution_idempotency_conflict"
                )
            record = existing
        else:
            # Every deterministic contract and current-authority check runs
            # before the first external write. Admission rechecks authority to
            # close the race between payload storage and the SQL transaction.
            service.validate_prepared_issue(prepared)
            self._store_prepared_payload(
                content=content,
                fingerprint=payload_fingerprint,
                created_by=str(created_by),
                expected_reference=payload_reference,
            )
            record = service.admit_prepared_issue(prepared)
        return self._ensure_bound_task_projection(
            record=record,
            assigned_worker_url=assigned_worker_url,
            destination_selection=normalized_destination_selection,
            source_access_intent=source_access_intent,
        )

    def publish_bound_task_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
        status_values: Mapping[str, Any] | None = None,
        event_type: str | None = None,
        event_actor: str = "knowledge-index-worker-gateway",
        event_details: Mapping[str, Any] | None = None,
    ) -> Any:
        """Publish one accepted v2 result through the atomic Task CAS.

        Completed results reach this seam only after the durable execution
        result and Source-Control completion projection have succeeded.  A
        publication failure is therefore Hub-local continuation work and must
        never be attributed to, or retried against, the Worker.
        """

        task = self._repository().get_by_id(str(job_id))
        if task is None:
            raise ValueError("knowledge_index_job_not_found")
        raw_task = self._task_mapping(task)
        envelope = dict(
            dict(raw_task.get("worker_execution_context") or {}).get(
                "knowledge_index_job"
            )
            or {}
        )
        if (
            str(envelope.get("schema") or "")
            != KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
            or str(envelope.get("job_id") or "") != str(job_id)
        ):
            raise ValueError(
                "knowledge_index_execution_task_binding_invalid"
            )
        payload = dict(result)
        status = str(payload.get("status") or "").strip().lower()
        details = {
            "idempotency_fingerprint": envelope.get(
                "idempotency_fingerprint"
            ),
            "worker_result_schema": payload.get("schema"),
            **dict(event_details or {}),
        }
        try:
            return self._result_publisher().publish(
                job_id=str(job_id),
                expected_envelope=envelope,
                result=payload,
                status_values=dict(status_values or {}),
                status_reason_code=(
                    str(payload.get("reason_code") or "") or None
                ),
                event_type=(
                    str(event_type or "").strip()
                    or f"knowledge_index_job_{status}"
                ),
                event_actor=str(event_actor),
                event_details=details,
            )
        except KnowledgeIndexCompletionProjectionPending:
            raise
        except Exception as exc:
            if status == "completed":
                raise KnowledgeIndexCompletionProjectionPending(
                    exc
                ) from exc
            raise

    def accept_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
        authenticated_worker_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate and persist a worker result through the existing task state path."""

        payload = self.validate_worker_result(
            job_id=job_id,
            result=result,
            authenticated_worker_id=authenticated_worker_id,
        )
        task = self._repository().get_by_id(str(job_id))
        raw_task = task.model_dump() if hasattr(task, "model_dump") else dict(task)
        status = str(payload.get("status") or "").strip().lower()
        envelope = dict((raw_task.get("worker_execution_context") or {}).get("knowledge_index_job") or {})
        bound_v2 = bool(
            str(envelope.get("schema") or "")
            == KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
        )
        if bound_v2:
            payload = self.materialize_worker_result(
                job_id=str(job_id),
                result=payload,
                task=raw_task,
                authenticated_worker_id=str(
                    authenticated_worker_id or ""
                ),
            )
            status = str(payload.get("status") or "").strip().lower()

        verification = dict(raw_task.get("verification_status") or {})
        verification["knowledge_index_job_result"] = payload
        if bound_v2:
            self.publish_bound_task_result(
                job_id=str(job_id),
                result=payload,
                status_values={"verification_status": verification},
                event_type=f"knowledge_index_job_{status}",
                event_actor="knowledge-index-worker-gateway",
            )
            return self.get_job(job_id) or {}

        from agent.services.task_runtime_service import update_local_task_status

        update_local_task_status(
            str(job_id),
            status,
            status_reason_code=str(payload.get("reason_code") or "") or None,
            verification_status=verification,
            event_type=f"knowledge_index_job_{status}",
            event_actor="knowledge-index-worker-gateway",
            event_details={
                "idempotency_fingerprint": envelope.get("idempotency_fingerprint"),
                "worker_result_schema": payload.get("schema"),
            },
        )
        return self.get_job(job_id) or {}

    def materialize_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
        task: Mapping[str, Any],
        authenticated_worker_id: str | None = None,
        transfer_deadline: Any | None = None,
    ) -> dict[str, Any]:
        """Validate and admit worker artifacts before a Hub task can complete."""

        self._require_transfer_deadline(transfer_deadline)
        payload = self.validate_worker_result(
            job_id=job_id,
            result=result,
            authenticated_worker_id=authenticated_worker_id,
        )
        service = self._worker_artifact_service
        if service is None:
            from agent.services.knowledge_index_worker_artifact_service import (
                KnowledgeIndexWorkerArtifactService,
            )

            service = KnowledgeIndexWorkerArtifactService()
        materialization_deadline = (
            {"transfer_deadline": transfer_deadline}
            if transfer_deadline is not None
            else {}
        )
        materialized = service.materialize(
            job_id=job_id,
            result=payload,
            task=task,
            **materialization_deadline,
        )
        self._require_transfer_deadline(transfer_deadline)
        raw_task = self._repository().get_by_id(str(job_id))
        raw_task_payload = (
            raw_task.model_dump()
            if hasattr(raw_task, "model_dump")
            else dict(raw_task or {})
        )
        envelope = dict(
            (raw_task_payload.get("worker_execution_context") or {}).get(
                "knowledge_index_job"
            )
            or {}
        )
        if (
            str(envelope.get("schema") or "")
            == KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
        ):
            if self._execution_binding_service is None:
                raise RuntimeError(
                    "knowledge_index_execution_binding_service_unavailable"
                )
            projection = None
            if str(materialized.get("status") or "") == "completed":
                if self._source_control_completion_projector is None:
                    raise RuntimeError(
                        "knowledge_index_source_projection_service_unavailable"
                    )
                complete_with_projection = getattr(
                    self._execution_binding_service,
                    "finalize_completed_result_with_projection",
                    None,
                )
                if not callable(complete_with_projection):
                    raise RuntimeError(
                        "knowledge_index_completion_projection_store_unavailable"
                    )
                self._require_transfer_deadline(transfer_deadline)
                _record, projection = complete_with_projection(
                    job_id=str(job_id),
                    worker_result=payload,
                    materialized_result=materialized,
                    authenticated_worker_id=str(
                        authenticated_worker_id or ""
                    ),
                )
            else:
                self._require_transfer_deadline(transfer_deadline)
                self._execution_binding_service.finalize_result(
                    job_id=str(job_id),
                    payload=payload,
                    authenticated_worker_id=str(
                        authenticated_worker_id or ""
                    ),
                )
            if projection is not None:
                try:
                    self._require_transfer_deadline(transfer_deadline)
                except Exception as exc:
                    # The completed-result CAS and its projection outbox are
                    # durable. Continue through the Hub-only saga instead of
                    # blaming or re-dispatching the Worker.
                    raise KnowledgeIndexCompletionProjectionPending(
                        exc
                    ) from exc
                return self._apply_completion_projection(
                    projection=projection,
                    task=raw_task_payload,
                    artifact_service=service,
                    transfer_deadline=transfer_deadline,
                )
        return materialized

    def reconcile_completion_projection(
        self,
        *,
        job_id: str,
        expected_projection_lock_version: int,
    ) -> dict[str, Any]:
        """Finish a durable completion saga without invoking its Worker."""

        binding_service = self._execution_binding_service
        if binding_service is None:
            raise RuntimeError(
                "knowledge_index_execution_binding_service_unavailable"
            )
        getter = getattr(
            binding_service,
            "get_completion_projection",
            None,
        )
        if not callable(getter):
            raise RuntimeError(
                "knowledge_index_completion_projection_store_unavailable"
            )
        projection = getter(str(job_id))
        if projection.lock_version != int(
            expected_projection_lock_version
        ):
            from agent.services.knowledge_index_execution_binding_service import (
                KnowledgeIndexExecutionBindingError,
            )

            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_completion_projection_conflict"
            )
        task = self._repository().get_by_id(str(job_id))
        if task is None:
            raise ValueError("knowledge_index_job_not_found")
        raw_task = (
            task.model_dump() if hasattr(task, "model_dump") else dict(task)
        )
        service = self._worker_artifact_service
        if service is None:
            from agent.services.knowledge_index_worker_artifact_service import (
                KnowledgeIndexWorkerArtifactService,
            )

            service = KnowledgeIndexWorkerArtifactService()
        materialized = self._apply_completion_projection(
            projection=projection,
            task=raw_task,
            artifact_service=service,
        )
        verification = dict(raw_task.get("verification_status") or {})
        verification["knowledge_index_job_result"] = materialized
        self.publish_bound_task_result(
            job_id=str(job_id),
            result=materialized,
            status_values={"verification_status": verification},
            event_type="knowledge_index_completion_reconciled",
            event_actor="knowledge-index-completion-reconciler",
            event_details={
                "projection_digest": projection.projection_digest,
            },
        )
        return self.get_job(str(job_id)) or {
            "job_id": str(job_id),
            "status": "completed",
        }

    def _apply_completion_projection(
        self,
        *,
        projection: Any,
        task: Mapping[str, Any],
        artifact_service: Any,
        transfer_deadline: Any | None = None,
    ) -> dict[str, Any]:
        payload = dict(projection.payload)
        materialized = dict(payload["materialized_result"])
        references = [
            dict(item)
            for item in list(payload.get("artifact_references") or [])
        ]
        context = dict(task.get("worker_execution_context") or {})
        envelope = dict(context.get("knowledge_index_job") or {})
        try:
            self._require_transfer_deadline(transfer_deadline)
            self._source_control_completion_projector.project(
                envelope=envelope,
                result=materialized,
                artifact_references=references,
            )
            self._require_transfer_deadline(transfer_deadline)
            activate = getattr(
                artifact_service,
                "activate_materialized_result",
                None,
            )
            if not callable(activate):
                raise RuntimeError(
                    "knowledge_index_worker_activation_service_unavailable"
                )
            activation_deadline = (
                {"transfer_deadline": transfer_deadline}
                if transfer_deadline is not None
                else {}
            )
            activated = activate(
                job_id=str(projection.job_id),
                result=materialized,
                artifact_references=references,
                task=task,
                **activation_deadline,
            )
            self._require_transfer_deadline(transfer_deadline)
            marker = getattr(
                self._execution_binding_service,
                "mark_completion_projection_projected",
                None,
            )
            if not callable(marker):
                raise RuntimeError(
                    "knowledge_index_completion_projection_store_unavailable"
                )
            marker(
                job_id=str(projection.job_id),
                expected_lock_version=int(projection.lock_version),
                expected_projection_digest=str(
                    projection.projection_digest
                ),
            )
            self._require_transfer_deadline(transfer_deadline)
            return activated
        except KnowledgeIndexCompletionProjectionPending:
            raise
        except Exception as exc:
            # Result CAS and the internal pending receipt are already durable.
            # The explicit Hub reconciler can replay this idempotent sequence
            # without another Worker dispatch, live lease, or mutable grant.
            raise KnowledgeIndexCompletionProjectionPending(exc) from exc

    @staticmethod
    def _require_transfer_deadline(transfer_deadline: Any | None) -> None:
        if transfer_deadline is not None:
            transfer_deadline.require_remaining_seconds()

    def validate_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
        authenticated_worker_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a schema-shaped result only when it is bound to the Hub task."""

        task = self._repository().get_by_id(str(job_id))
        if task is None:
            raise ValueError("knowledge_index_job_not_found")
        raw_task = task.model_dump() if hasattr(task, "model_dump") else dict(task)
        envelope = dict((raw_task.get("worker_execution_context") or {}).get("knowledge_index_job") or {})
        if (
            str(envelope.get("schema") or "")
            == KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
        ):
            if self._execution_binding_service is None:
                raise RuntimeError(
                    "knowledge_index_execution_binding_service_unavailable"
                )
            _record, parsed = (
                self._execution_binding_service.validate_result(
                    job_id=str(job_id),
                    payload=dict(result),
                    authenticated_worker_id=str(
                        authenticated_worker_id or ""
                    ),
                )
            )
            payload = parsed.to_wire()
            artifact_refs = payload.get("artifact_refs")
            if not isinstance(artifact_refs, list):
                raise ValueError(
                    "knowledge_index_result_artifact_refs_invalid"
                )
            for reference in artifact_refs:
                _validate_worker_artifact_reference(reference)
            return payload
        if str(envelope.get("schema") or "") != KNOWLEDGE_INDEX_JOB_SCHEMA:
            raise ValueError("knowledge_index_job_schema_invalid")
        payload = dict(result or {})
        missing = _WORKER_RESULT_FIELDS - set(payload)
        unknown = set(payload) - _WORKER_RESULT_FIELDS
        if missing:
            raise ValueError("knowledge_index_result_fields_missing")
        if unknown:
            raise ValueError("knowledge_index_result_fields_unknown")
        if str(payload.get("schema") or "") != KNOWLEDGE_INDEX_RESULT_SCHEMA:
            raise ValueError("knowledge_index_result_schema_invalid")
        if str(payload.get("job_id") or "") != str(job_id):
            raise ValueError("knowledge_index_result_job_mismatch")
        if str(envelope.get("job_id") or "") != str(job_id):
            raise ValueError("knowledge_index_job_binding_mismatch")
        fingerprint = str(payload.get("idempotency_fingerprint") or "")
        if fingerprint != str(envelope.get("idempotency_fingerprint") or ""):
            raise ValueError("knowledge_index_result_fingerprint_mismatch")
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise ValueError("knowledge_index_result_fingerprint_invalid")
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"completed", "failed"}:
            raise ValueError("knowledge_index_result_status_invalid")
        if payload.get("reason_code") is not None and not isinstance(payload.get("reason_code"), str):
            raise ValueError("knowledge_index_result_reason_code_invalid")
        for field in ("knowledge_index", "run"):
            value = payload.get(field)
            if value is not None and not isinstance(value, Mapping):
                raise ValueError(f"knowledge_index_result_{field}_invalid")
        results = payload.get("results")
        if results is not None and (
            not isinstance(results, list)
            or any(not isinstance(item, Mapping) for item in results)
        ):
            raise ValueError("knowledge_index_result_results_invalid")
        artifact_refs = payload.get("artifact_refs")
        if not isinstance(artifact_refs, list):
            raise ValueError("knowledge_index_result_artifact_refs_invalid")
        for reference in artifact_refs:
            _validate_worker_artifact_reference(reference)
        if payload.get("error") is not None and not isinstance(payload.get("error"), str):
            raise ValueError("knowledge_index_result_error_invalid")
        return {
            **payload,
            "status": status,
            "knowledge_index": dict(payload["knowledge_index"])
            if isinstance(payload.get("knowledge_index"), Mapping)
            else None,
            "run": dict(payload["run"]) if isinstance(payload.get("run"), Mapping) else None,
            "results": [dict(item) for item in results] if isinstance(results, list) else None,
            "artifact_refs": [dict(item) for item in artifact_refs],
        }

    def _submit(
        self,
        *,
        job_type: str,
        scope_id: str,
        created_by: str | None,
        profile_name: str | None,
        payload: dict[str, Any],
        source_scope: str | None = None,
    ) -> dict[str, Any]:
        intent = {
            "job_type": job_type,
            "scope_id": scope_id,
            "source_scope": source_scope,
            "profile_name": str(profile_name or "default"),
            "payload": payload,
        }
        rendered = _canonical_json(intent)
        if len(rendered) > _MAX_JOB_PAYLOAD_BYTES:
            raise ValueError("knowledge_index_job_payload_too_large")
        idempotency_fingerprint = hashlib.sha256(rendered).hexdigest()
        job_id = f"knowledge-index-{idempotency_fingerprint[:32]}"
        existing = self.get_job(job_id)
        if existing is not None:
            return existing
        payload_bytes = _canonical_json(payload)
        worker_payload = payload
        payload_artifact_ref = None
        if len(rendered) > _INLINE_JOB_PAYLOAD_BYTES:
            payload_fingerprint = hashlib.sha256(
                payload_bytes
            ).hexdigest()
            payload_artifact_ref = self._store_large_payload(
                content=payload_bytes,
                fingerprint=payload_fingerprint,
                created_by=created_by,
            )
            worker_payload = {"payload_artifact_ref": payload_artifact_ref}
        now = float(self._clock())
        envelope = {
            "schema": KNOWLEDGE_INDEX_JOB_SCHEMA,
            "job_id": job_id,
            "job_type": job_type,
            "scope_id": scope_id,
            "source_scope": source_scope,
            "profile_name": str(profile_name or "default"),
            "created_by": created_by,
            "created_at": now,
            "idempotency_fingerprint": idempotency_fingerprint,
            "record_count": len(list(payload.get("records") or [])),
            "artifact_ids": list(payload.get("artifact_ids") or []),
            "payload": worker_payload,
        }
        self._queue().ingest_task(
            task_id=job_id,
            status="todo",
            title=f"Knowledge index: {job_type} {scope_id}"[:200],
            description="Worker-delegated persistent CodeCompass indexing job.",
            priority="medium",
            created_by=created_by or "knowledge-index-api",
            source="knowledge_index",
            tags=["knowledge_index", "hub_delegated", "persistent_job"],
            event_type="task_ingested",
            event_channel="hub_task_queue",
            event_details={
                "job_type": job_type,
                "scope_id": scope_id,
                "idempotency_fingerprint": idempotency_fingerprint,
                "domain_event_type": "knowledge_index_job_queued",
            },
            extra_fields={
                "task_kind": "codecompass_index_build",
                "retrieval_intent": "index_snapshot",
                "required_context_scope": source_scope or "artifact",
                "required_capabilities": ["retrieval", "index_write"],
                "worker_execution_context": {"knowledge_index_job": envelope},
                "verification_spec": {
                    "schema": KNOWLEDGE_INDEX_RESULT_SCHEMA,
                "artifact_first": True,
                "idempotency_fingerprint": idempotency_fingerprint,
                "payload_artifact_ref": payload_artifact_ref,
            },
            },
        )
        created = self.get_job(job_id)
        if created is None:
            raise RuntimeError("knowledge_index_job_persistence_failed")
        return created


knowledge_index_job_service = KnowledgeIndexJobService()


def get_knowledge_index_job_service() -> KnowledgeIndexJobService:
    return knowledge_index_job_service


__all__ = [
    "KNOWLEDGE_INDEX_JOB_SCHEMA",
    "KNOWLEDGE_INDEX_RESULT_SCHEMA",
    "KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA",
    "KNOWLEDGE_INDEX_EXECUTION_RESULT_SCHEMA",
    "KnowledgeIndexJobService",
    "get_knowledge_index_job_service",
]
