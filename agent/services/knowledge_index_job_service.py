"""Persistent Hub-owned orchestration for delegated knowledge-index jobs.

``KnowledgeIndexJobService`` is the public entry point.  It owns queue
ingestion and task-state publication and composes narrow collaborators,
each overridable through a keyword-only constructor parameter:

* ``knowledge_index_job_contract`` -- schemas, canonical encoding, job view
* ``knowledge_index_worker_result_references`` -- Worker artifact references
* ``knowledge_index_job_payload_storage`` -- large payload artifacts
* ``knowledge_index_bound_task_projection`` -- bound task projection rules
* ``knowledge_index_bound_dispatch_gate`` -- mandatory Hub dispatch gate
* ``knowledge_index_bound_job_admission`` -- bound execution admission
* ``knowledge_index_completion_saga`` -- durable completion projection
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from typing import Any

from agent.services.knowledge_index_bound_dispatch_gate import (
    KnowledgeIndexBoundDispatchGate,
    persist_bound_execution_envelope,
)
from agent.services.knowledge_index_bound_job_admission import (
    KnowledgeIndexBoundJobAdmission,
)
from agent.services.knowledge_index_bound_task_projection import (
    bound_task_ingest_request,
    expired_dispatch_reconciliation_marker,
    project_expired_dispatch_failure,
    task_has_expired_dispatch_projection,
    task_matches_bound_execution,
    validate_bound_task_projection,
)
from agent.services.knowledge_index_completion_saga import (
    KnowledgeIndexCompletionSaga,
    require_transfer_deadline,
)
from agent.services.knowledge_index_job_contract import (
    INLINE_JOB_PAYLOAD_BYTES,
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    KNOWLEDGE_INDEX_EXECUTION_RESULT_SCHEMA,
    KNOWLEDGE_INDEX_JOB_SCHEMA,
    KNOWLEDGE_INDEX_RESULT_SCHEMA,
    MAX_JOB_PAYLOAD_BYTES,
    RECONCILABLE_TASK_STATUSES,
    canonical_json,
    legacy_job_ingest_request,
    normalize_graph_visual_metrics_options,
    project_job_view,
    task_mapping,
    validate_legacy_worker_result,
)
from agent.services.knowledge_index_job_payload_storage import (
    KnowledgeIndexJobPayloadStorage,
)
from agent.services.knowledge_index_job_ports import (
    KnowledgeIndexCompletionProjectionPending as KnowledgeIndexCompletionProjectionPending,
)
from agent.services.knowledge_index_job_ports import (
    KnowledgeIndexJobRepositoryPort as KnowledgeIndexJobRepositoryPort,
)
from agent.services.knowledge_index_job_ports import (
    KnowledgeIndexPayloadStorePort as KnowledgeIndexPayloadStorePort,
)
from agent.services.knowledge_index_job_ports import (
    KnowledgeIndexTaskQueuePort as KnowledgeIndexTaskQueuePort,
)
from agent.services.knowledge_index_job_ports import (
    KnowledgeIndexWorkerDirectoryPort as KnowledgeIndexWorkerDirectoryPort,
)
from agent.services.knowledge_index_worker_result_references import (
    validate_worker_artifact_references,
)
from ananta_contracts.knowledge_index_execution import (
    KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
)


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
        payload_storage: KnowledgeIndexJobPayloadStorage | None = None,
        dispatch_gate: KnowledgeIndexBoundDispatchGate | None = None,
        bound_job_admission: KnowledgeIndexBoundJobAdmission | None = None,
        completion_saga: KnowledgeIndexCompletionSaga | None = None,
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
        self._payload_storage = payload_storage or KnowledgeIndexJobPayloadStorage(
            payload_store=payload_store
        )
        self._dispatch_gate = dispatch_gate or KnowledgeIndexBoundDispatchGate(
            repository_provider=self._repository,
            execution_binding_service=execution_binding_service,
            destination_resolution_service=destination_resolution_service,
            source_access_enforcement_service=(
                source_access_enforcement_service
            ),
            allow_legacy_unresolved_destination=(
                allow_legacy_unresolved_destination
            ),
            allow_legacy_unsigned_source_dispatch=(
                allow_legacy_unsigned_source_dispatch
            ),
            clock=clock,
        )
        self._bound_job_admission = (
            bound_job_admission
            or KnowledgeIndexBoundJobAdmission(
                repository_provider=self._repository,
                payload_storage=self._payload_storage,
                execution_binding_service=execution_binding_service,
                destination_resolution_service=(
                    destination_resolution_service
                ),
                worker_directory=worker_directory,
                allow_legacy_unresolved_destination=(
                    allow_legacy_unresolved_destination
                ),
            )
        )
        self._completion_saga = completion_saga or KnowledgeIndexCompletionSaga(
            execution_binding_service=execution_binding_service,
            source_control_completion_projector=(
                source_control_completion_projector
            ),
        )

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

    def authorize_bound_worker_dispatch(
        self,
        *,
        job_id: str,
        authenticated_worker_id: str,
        destination_selection: Mapping[str, Any] | None = None,
        dispatch_phase: str = "execute",
    ) -> dict[str, Any]:
        """Return v2 execution context only after the mandatory Hub gate."""

        return self._dispatch_gate.authorize(
            job_id=job_id,
            authenticated_worker_id=authenticated_worker_id,
            destination_selection=destination_selection,
            dispatch_phase=dispatch_phase,
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
        persist_bound_execution_envelope(
            self._repository(),
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
        marker = expired_dispatch_reconciliation_marker(record)
        result = status_cas(
            normalized_job_id,
            expected_statuses=set(RECONCILABLE_TASK_STATUSES),
            target_status="failed",
            predicate=lambda task: task_matches_bound_execution(
                task,
                expected_envelope=record.job.to_wire(),
            ),
            mutate=lambda task: project_expired_dispatch_failure(
                task,
                marker=marker,
            ),
        )
        projected_task = getattr(result, "task", None)
        if not bool(getattr(result, "updated", False)) and not (
            projected_task is not None
            and task_has_expired_dispatch_projection(
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

    def _ensure_bound_task_projection(
        self,
        *,
        record: Any,
        assigned_worker_url: str,
        destination_selection: Mapping[str, Any],
        source_access_intent: Mapping[str, Any],
    ) -> dict[str, Any]:
        task = self._repository().get_by_id(record.job.job_id)
        if task is None:
            try:
                self._queue().ingest_task(
                    **bound_task_ingest_request(
                        record=record,
                        assigned_worker_url=assigned_worker_url,
                        destination_selection=destination_selection,
                        source_access_intent=source_access_intent,
                    )
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
        validate_bound_task_projection(
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
        verification = dict(raw.get("verification_status") or {})
        result = verification.get("knowledge_index_job_result")
        payload = project_job_view(raw, envelope)
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
                "graph_visual_metrics": normalize_graph_visual_metrics_options(
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
                "graph_visual_metrics": normalize_graph_visual_metrics_options(
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
                "graph_visual_metrics": normalize_graph_visual_metrics_options(
                    graph_visual_metrics
                ),
            },
        )

    def submit_bound_source_revision_job(self, **submission: Any) -> dict[str, Any]:
        """Admit one immutable source-revision job and project its Hub task.

        Accepts the keyword-only parameters of
        :meth:`KnowledgeIndexBoundJobAdmission.admit`.
        """

        admitted = self._bound_job_admission.admit(**submission)
        return self._ensure_bound_task_projection(
            record=admitted.record,
            assigned_worker_url=admitted.assigned_worker_url,
            destination_selection=admitted.destination_selection,
            source_access_intent=admitted.source_access_intent,
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
        raw_task = task_mapping(task)
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

        require_transfer_deadline(transfer_deadline)
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
        require_transfer_deadline(transfer_deadline)
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
            return self._completion_saga.finalize(
                job_id=str(job_id),
                worker_result=payload,
                materialized=materialized,
                task=raw_task_payload,
                artifact_service=service,
                authenticated_worker_id=authenticated_worker_id,
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
        materialized = self._completion_saga.apply(
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
            validate_worker_artifact_references(
                artifact_refs,
                execution_envelope=envelope,
            )
            return payload
        if str(envelope.get("schema") or "") != KNOWLEDGE_INDEX_JOB_SCHEMA:
            raise ValueError("knowledge_index_job_schema_invalid")
        return validate_legacy_worker_result(
            job_id=job_id,
            result=result,
            envelope=envelope,
        )

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
        rendered = canonical_json(intent)
        if len(rendered) > MAX_JOB_PAYLOAD_BYTES:
            raise ValueError("knowledge_index_job_payload_too_large")
        idempotency_fingerprint = hashlib.sha256(rendered).hexdigest()
        job_id = f"knowledge-index-{idempotency_fingerprint[:32]}"
        existing = self.get_job(job_id)
        if existing is not None:
            return existing
        payload_bytes = canonical_json(payload)
        worker_payload = payload
        payload_artifact_ref = None
        if len(rendered) > INLINE_JOB_PAYLOAD_BYTES:
            payload_fingerprint = hashlib.sha256(
                payload_bytes
            ).hexdigest()
            payload_artifact_ref = self._payload_storage.store(
                content=payload_bytes,
                fingerprint=payload_fingerprint,
                created_by=created_by,
            )
            worker_payload = {"payload_artifact_ref": payload_artifact_ref}
        self._queue().ingest_task(
            **legacy_job_ingest_request(
                job_id=job_id,
                job_type=job_type,
                scope_id=scope_id,
                source_scope=source_scope,
                profile_name=profile_name,
                created_by=created_by,
                created_at=float(self._clock()),
                idempotency_fingerprint=idempotency_fingerprint,
                payload=payload,
                worker_payload=worker_payload,
                payload_artifact_ref=payload_artifact_ref,
            )
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
