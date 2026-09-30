"""Worker-side execution boundary for Hub-delegated knowledge-index jobs.

The task handler validates the Hub envelope and owns the result contract. The
wire contract and ports live in ``knowledge_index_job_contract``; execution,
payload loading and artifact publication are composed collaborators in their
own modules and are re-exported here for existing importers.
"""

from __future__ import annotations

import hmac
import time
from collections.abc import Mapping
from typing import Any

from worker.retrieval.knowledge_index_artifact_publisher import (
    WorkerKnowledgeIndexArtifactPublisher,
)
from worker.retrieval.knowledge_index_execution_guard import (
    DeadlineAwareKnowledgeIndexExecutionRunner,
    KnowledgeIndexExecutionDeadlineError,
    KnowledgeIndexExecutionGuardPort,
    MonotonicKnowledgeIndexExecutionGuard,
)
from worker.retrieval.knowledge_index_job_contract import (
    BOUND_JOB_SCHEMA,
    BOUND_RESULT_SCHEMA,
    JOB_SCHEMA,
    MAX_PAYLOAD_BYTES,
    PAYLOAD_MEDIA_TYPE,
    RESULT_SCHEMA,
    SOURCE_ACCESS_MANIFEST_FIELD,
    KnowledgeIndexArtifactPublisherPort,
    KnowledgeIndexExecutionPort,
    KnowledgeIndexGraphArtifactMaterializerPort,
    KnowledgeIndexPayloadLoaderPort,
    KnowledgeIndexWorkerDispatchAdmissionPort,
)
from worker.retrieval.knowledge_index_payload_loader import (
    HubArtifactKnowledgeIndexPayloadLoader,
    _KnowledgeIndexPayloadNoRedirectHandler,
)
from worker.retrieval.knowledge_index_rag_helper_execution import (
    RagHelperKnowledgeIndexExecution,
)

# Compatibility re-exports: names that were importable from this module
# before its collaborators were extracted.
from ananta_contracts.codecompass_domain_supplement import (  # noqa: F401,I001
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
)
from ananta_contracts.codecompass_graph_limits import (  # noqa: F401
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)
from ananta_contracts.knowledge_index_execution import (  # noqa: F401
    MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES,
)
from worker.retrieval.knowledge_index_execution_guard import (  # noqa: F401
    KnowledgeIndexExecutionDeadlinePort,
)
from worker.retrieval.knowledge_index_payload_loader import (  # noqa: F401
    _PAYLOAD_READ_CHUNK_BYTES,
)


class KnowledgeIndexWorkerTaskHandler:
    """Validate one Hub envelope, execute once, and return an immutable result."""

    def __init__(
        self,
        execution: KnowledgeIndexExecutionPort,
        *,
        source_access_manifest_verifier: Any | None = None,
        worker_id: str | None = None,
        allow_legacy_unsigned_source_dispatch: bool = False,
        worker_dispatch_admission: (
            KnowledgeIndexWorkerDispatchAdmissionPort | None
        ) = None,
        require_bound_dispatch_marker: bool = False,
        execution_guard: KnowledgeIndexExecutionGuardPort | None = None,
        execution_runner: DeadlineAwareKnowledgeIndexExecutionRunner | None = None,
        clock_ms=lambda: int(time.time() * 1000),
    ) -> None:
        self._execution = execution
        self._source_access_manifest_verifier = (
            source_access_manifest_verifier
        )
        self._worker_id = str(worker_id or "").strip()
        self._allow_legacy_unsigned_source_dispatch = bool(
            allow_legacy_unsigned_source_dispatch
        )
        self._worker_dispatch_admission = worker_dispatch_admission
        self._require_bound_dispatch_marker = bool(
            require_bound_dispatch_marker
        )
        self._execution_guard = (
            execution_guard or MonotonicKnowledgeIndexExecutionGuard()
        )
        self._execution_runner = (
            execution_runner or DeadlineAwareKnowledgeIndexExecutionRunner()
        )
        self._clock_ms = clock_ms

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        """Expose a non-shell executable marker for the deterministic pipeline."""

        job = self._resolve_job(None, kwargs)
        prepared_dispatch = self._prepare_bound_dispatch(
            job=job,
            kwargs=kwargs,
            phase="propose",
        )
        if prepared_dispatch is not None:
            job = dict(prepared_dispatch.executable_job)
        self._validate_job(job, require_source_access=False)
        return {
            "proposal_id": f"{job['job_id']}-proposal",
            "strategy_id": "deterministic_handler",
            "command": None,
            "tool_calls": [
                {
                    "name": "codecompass_index_build",
                    "arguments": {"job_id": job["job_id"]},
                }
            ],
            "expected_artifacts": [
                {
                    "kind": "knowledge_index_manifest",
                    "required": True,
                    "schema": RESULT_SCHEMA,
                }
            ],
            "safety_flags": {
                "worker_only": True,
                "network_access": "hub_artifact_only",
            },
        }

    def execute(
        self,
        envelope: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        job = self._resolve_job(envelope, kwargs)
        prepared_dispatch = self._prepare_bound_dispatch(
            job=job,
            kwargs=kwargs,
            phase="execute",
        )
        if prepared_dispatch is not None:
            job = dict(prepared_dispatch.executable_job)
        self._validate_job(job, require_source_access=True)
        claimed_dispatch = None
        if prepared_dispatch is not None:
            claimed_dispatch = self._worker_dispatch_admission.claim_execute(
                task_id=str(job.get("job_id") or ""),
                prepared=prepared_dispatch,
            )
            job = dict(claimed_dispatch.executable_job)
            self._apply_claimed_dispatch_to_request_task(
                kwargs.get("task"),
                job=job,
            )
            replayed_result = claimed_dispatch.replayed_result
            if replayed_result is not None:
                return dict(replayed_result)
        try:
            if str(job.get("schema") or "") == BOUND_JOB_SCHEMA:
                from ananta_contracts.knowledge_index_execution import (
                    parse_execution_job,
                )

                parsed_job = parse_execution_job(
                    self._bound_contract_payload(job)
                )
                execution_deadline = self._execution_guard.start(
                    max_runtime_seconds=(
                        parsed_job.resources.max_runtime_seconds
                    ),
                )
                raw_result = dict(
                    self._execution_runner.execute(
                        self._execution,
                        job,
                        execution_deadline=execution_deadline,
                    )
                    or {}
                )
            else:
                raw_result = dict(self._execution.execute(job) or {})
        except KnowledgeIndexExecutionDeadlineError as exc:
            return self._complete_claimed_result(
                claimed_dispatch,
                self._result(
                    job,
                    status="failed",
                    reason_code=exc.reason_code,
                    error=exc.reason_code,
                ),
            )
        except Exception as exc:
            return self._complete_claimed_result(
                claimed_dispatch,
                self._result(
                    job,
                    status="failed",
                    reason_code=(
                        "worker_execution_failed:"
                        f"{type(exc).__name__}"
                    ),
                    error=str(exc)[:1000],
                ),
            )
        allowed_result_fields = {
            "status",
            "reason_code",
            "knowledge_index",
            "run",
            "results",
            "artifact_refs",
            "error",
        }
        if set(raw_result) - allowed_result_fields:
            return self._complete_claimed_result(
                claimed_dispatch,
                self._result(
                    job,
                    status="failed",
                    reason_code="worker_result_fields_unknown",
                    error=(
                        "execution port returned unauthorized fields"
                    ),
                ),
            )
        status = str(raw_result.get("status") or "").strip().lower()
        if status not in {"completed", "failed"}:
            return self._complete_claimed_result(
                claimed_dispatch,
                self._result(
                    job,
                    status="failed",
                    reason_code="worker_result_status_invalid",
                    error=(
                        "execution port returned a non-terminal status"
                    ),
                ),
            )
        return self._complete_claimed_result(
            claimed_dispatch,
            self._result(
                job,
                status=status,
                reason_code=(
                    str(raw_result.get("reason_code") or "") or None
                ),
                knowledge_index=raw_result.get("knowledge_index"),
                run=raw_result.get("run"),
                results=raw_result.get("results"),
                artifact_refs=list(
                    raw_result.get("artifact_refs") or []
                ),
                error=str(raw_result.get("error") or "") or None,
            ),
        )

    def _complete_claimed_result(
        self,
        claimed_dispatch: Any | None,
        result_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if claimed_dispatch is None:
            return dict(result_payload)
        admission = self._worker_dispatch_admission
        if admission is None:
            raise ValueError(
                "knowledge_index_worker_dispatch_admission_unavailable"
            )
        return dict(
            admission.complete_execute_result(
                claimed=claimed_dispatch,
                result_payload=result_payload,
            )
        )

    @staticmethod
    def _resolve_job(
        envelope: Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
    ) -> dict[str, Any]:
        if (
            isinstance(envelope, Mapping)
            and str(envelope.get("schema") or "")
            in {JOB_SCHEMA, BOUND_JOB_SCHEMA}
        ):
            return dict(envelope)
        task = kwargs.get("task")
        if isinstance(task, Mapping):
            context = task.get("worker_execution_context")
            if isinstance(context, Mapping):
                job = context.get("knowledge_index_job")
                if isinstance(job, Mapping):
                    return dict(job)
        raise ValueError("knowledge_index_job_envelope_missing")

    def _prepare_bound_dispatch(
        self,
        *,
        job: Mapping[str, Any],
        kwargs: Mapping[str, Any],
        phase: str,
    ) -> Any | None:
        if str(job.get("schema") or "") != BOUND_JOB_SCHEMA:
            return None
        admission = self._worker_dispatch_admission
        if admission is None and not self._require_bound_dispatch_marker:
            # Backward-compatible programmatic v2 path. Production Worker
            # composition always enables the network dispatch boundary.
            return None
        if admission is None:
            raise ValueError(
                "knowledge_index_worker_dispatch_admission_unavailable"
            )
        task = kwargs.get("task")
        if not isinstance(task, Mapping):
            raise ValueError("knowledge_index_worker_dispatch_task_missing")
        return admission.prepare(
            task=task,
            job=job,
            request_data=kwargs.get("request_data"),
            expected_phase=phase,
        )

    @staticmethod
    def _apply_claimed_dispatch_to_request_task(
        task: Any,
        *,
        job: Mapping[str, Any],
    ) -> None:
        if not isinstance(task, dict):
            return
        context = dict(task.get("worker_execution_context") or {})
        context["knowledge_index_job"] = dict(job)
        context.pop("knowledge_index_dispatch_receipt", None)
        task["worker_execution_context"] = context

    def _validate_job(
        self,
        job: Mapping[str, Any],
        *,
        require_source_access: bool,
    ) -> None:
        if str(job.get("schema") or "") == BOUND_JOB_SCHEMA:
            from ananta_contracts.knowledge_index_execution import (
                parse_execution_job,
            )

            parsed = parse_execution_job(
                self._bound_contract_payload(job)
            )
            if (
                int(self._clock_ms())
                >= parsed.assignment.lease_expires_epoch_ms
            ):
                raise ValueError("knowledge_index_execution_lease_stale")
            self._validate_bound_worker_assignment(parsed)
            if require_source_access:
                self._validate_source_access_manifest(job, parsed)
            elif job.get(SOURCE_ACCESS_MANIFEST_FIELD) is not None:
                raise ValueError(
                    "knowledge_index_proposal_source_access_forbidden"
                )
            return
        if str(job.get("schema") or "") != JOB_SCHEMA:
            raise ValueError("knowledge_index_job_schema_invalid")
        if not str(job.get("job_id") or "").startswith("knowledge-index-"):
            raise ValueError("knowledge_index_job_id_invalid")
        fingerprint = str(job.get("idempotency_fingerprint") or "")
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise ValueError("knowledge_index_job_fingerprint_invalid")
        if str(job.get("job_type") or "") not in {"artifact", "collection", "source_records"}:
            raise ValueError("knowledge_index_job_type_invalid")
        if not isinstance(job.get("payload"), Mapping):
            raise ValueError("knowledge_index_job_payload_invalid")

    def _validate_bound_worker_assignment(self, parsed: Any) -> None:
        if not self._worker_id:
            raise ValueError(
                "knowledge_index_authenticated_worker_missing"
            )
        if not hmac.compare_digest(
            self._worker_id,
            parsed.assignment.worker_id,
        ):
            raise ValueError(
                "knowledge_index_assignment_worker_mismatch"
            )

    def _validate_source_access_manifest(
        self,
        job: Mapping[str, Any],
        parsed: Any,
    ) -> None:
        raw_manifest = job.get(SOURCE_ACCESS_MANIFEST_FIELD)
        if raw_manifest is None:
            if self._allow_legacy_unsigned_source_dispatch:
                return
            raise ValueError(
                "knowledge_index_source_access_manifest_missing"
            )
        if not isinstance(raw_manifest, Mapping):
            raise ValueError(
                "knowledge_index_source_access_manifest_invalid"
            )
        verifier = self._source_access_manifest_verifier
        if verifier is None or not verifier.verify_manifest(raw_manifest):
            raise ValueError(
                "knowledge_index_source_access_signature_invalid"
            )
        if int(self._clock_ms()) >= int(
            raw_manifest["grant_expires_at_epoch_ms"]
        ):
            raise ValueError(
                "knowledge_index_source_access_grant_expired"
            )
        authority = parsed.authority_binding
        expected = {
            "tenant_id": authority.tenant_id,
            "project_id": authority.project_id,
            "source_revision_id": authority.source_revision_id,
            "source_revision_digest": (
                authority.source_revision_digest
            ),
            "destination_id": authority.destination_id,
            "destination_digest": authority.destination_digest,
            "source_access_grant_id": (
                authority.source_access_grant_id
            ),
            "source_access_grant_digest": (
                authority.source_access_grant_digest
            ),
            "policy_digest": authority.policy_snapshot_digest,
            "content_manifest_digest": (
                parsed.file_manifest.manifest_digest
            ),
            "assignment_id": parsed.assignment.assignment_id,
            "lease_id": parsed.assignment.lease_id,
        }
        for field, expected_value in expected.items():
            if not hmac.compare_digest(
                str(raw_manifest.get(field) or ""),
                str(expected_value),
            ):
                raise ValueError(
                    f"knowledge_index_source_access_{field}_mismatch"
                )

    @staticmethod
    def _bound_contract_payload(
        job: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            key: value
            for key, value in job.items()
            if key != SOURCE_ACCESS_MANIFEST_FIELD
        }

    @staticmethod
    def _result(
        job: Mapping[str, Any],
        *,
        status: str,
        reason_code: str | None,
        knowledge_index: Any = None,
        run: Any = None,
        results: Any = None,
        artifact_refs: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if str(job.get("schema") or "") == BOUND_JOB_SCHEMA:
            from ananta_contracts.knowledge_index_execution import (
                KnowledgeIndexExecutionResult,
                parse_execution_job,
            )

            return KnowledgeIndexExecutionResult.create(
                parse_execution_job(
                    KnowledgeIndexWorkerTaskHandler._bound_contract_payload(
                        job
                    )
                ),
                status=status,
                reason_code=reason_code,
                knowledge_index=(
                    knowledge_index
                    if isinstance(knowledge_index, Mapping)
                    else None
                ),
                run=run if isinstance(run, Mapping) else None,
                results=(
                    [
                        item
                        for item in list(results or [])
                        if isinstance(item, Mapping)
                    ]
                    or None
                ),
                artifact_refs=[
                    item
                    for item in list(artifact_refs or [])
                    if isinstance(item, Mapping)
                ],
                error=error,
            ).to_wire()
        return {
            "schema": RESULT_SCHEMA,
            "job_id": str(job.get("job_id") or ""),
            "idempotency_fingerprint": str(job.get("idempotency_fingerprint") or ""),
            "status": status,
            "reason_code": reason_code,
            "knowledge_index": dict(knowledge_index) if isinstance(knowledge_index, Mapping) else None,
            "run": dict(run) if isinstance(run, Mapping) else None,
            "results": [dict(item) for item in list(results or []) if isinstance(item, Mapping)] or None,
            "artifact_refs": [dict(item) for item in list(artifact_refs or []) if isinstance(item, Mapping)],
            "error": error,
        }


def build_knowledge_index_task_handler(
    index_service: Any | None = None,
    *,
    payload_loader: KnowledgeIndexPayloadLoaderPort | None = None,
    artifact_publisher: KnowledgeIndexArtifactPublisherPort | None = None,
    graph_artifact_materializer: KnowledgeIndexGraphArtifactMaterializerPort | None = None,
    source_access_manifest_verifier: Any | None = None,
    worker_id: str | None = None,
    allow_legacy_unsigned_source_dispatch: bool = False,
    worker_dispatch_admission: (
        KnowledgeIndexWorkerDispatchAdmissionPort | None
    ) = None,
    require_bound_dispatch_marker: bool = False,
    execution_guard: KnowledgeIndexExecutionGuardPort | None = None,
    execution_runner: DeadlineAwareKnowledgeIndexExecutionRunner | None = None,
) -> KnowledgeIndexWorkerTaskHandler:
    """Composition hook used by the worker-only application bootstrap."""

    if index_service is None:
        from agent.services.rag_helper_index_service import get_rag_helper_index_service

        index_service = get_rag_helper_index_service()
    return KnowledgeIndexWorkerTaskHandler(
        RagHelperKnowledgeIndexExecution(
            index_service,
            payload_loader=payload_loader,
            artifact_publisher=artifact_publisher,
            graph_artifact_materializer=graph_artifact_materializer,
        ),
        source_access_manifest_verifier=source_access_manifest_verifier,
        worker_id=worker_id,
        allow_legacy_unsigned_source_dispatch=(
            allow_legacy_unsigned_source_dispatch
        ),
        worker_dispatch_admission=worker_dispatch_admission,
        require_bound_dispatch_marker=require_bound_dispatch_marker,
        execution_guard=execution_guard,
        execution_runner=execution_runner,
    )


__all__ = [
    "BOUND_JOB_SCHEMA",
    "BOUND_RESULT_SCHEMA",
    "JOB_SCHEMA",
    "MAX_PAYLOAD_BYTES",
    "PAYLOAD_MEDIA_TYPE",
    "RESULT_SCHEMA",
    "SOURCE_ACCESS_MANIFEST_FIELD",
    "KnowledgeIndexExecutionPort",
    "KnowledgeIndexPayloadLoaderPort",
    "KnowledgeIndexArtifactPublisherPort",
    "KnowledgeIndexGraphArtifactMaterializerPort",
    "KnowledgeIndexWorkerDispatchAdmissionPort",
    "KnowledgeIndexWorkerTaskHandler",
    "HubArtifactKnowledgeIndexPayloadLoader",
    "_KnowledgeIndexPayloadNoRedirectHandler",
    "WorkerKnowledgeIndexArtifactPublisher",
    "RagHelperKnowledgeIndexExecution",
    "build_knowledge_index_task_handler",
]
