"""Worker-side execution boundary for Hub-delegated knowledge-index jobs."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)
from ananta_contracts.knowledge_index_execution import (
    MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES,
)
from worker.retrieval.knowledge_index_execution_guard import (
    DeadlineAwareKnowledgeIndexExecutionRunner,
    KnowledgeIndexExecutionDeadlineError,
    KnowledgeIndexExecutionDeadlinePort,
    KnowledgeIndexExecutionGuardPort,
    MonotonicKnowledgeIndexExecutionGuard,
)

JOB_SCHEMA = "ananta.knowledge_index_job.v1"
RESULT_SCHEMA = "ananta.knowledge_index_job_result.v1"
BOUND_JOB_SCHEMA = "ananta.knowledge_index_execution_job.v2"
BOUND_RESULT_SCHEMA = "ananta.knowledge_index_execution_result.v2"
PAYLOAD_MEDIA_TYPE = "application/vnd.ananta.knowledge-index-job+json"
MAX_PAYLOAD_BYTES = MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES
_PAYLOAD_READ_CHUNK_BYTES = 1024 * 1024
SOURCE_ACCESS_MANIFEST_FIELD = "source_access_enforcement_manifest"


class _KnowledgeIndexPayloadNoRedirectHandler(
    urllib.request.HTTPRedirectHandler
):
    """Keep a delegated payload capability on the configured Hub request."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class KnowledgeIndexExecutionPort(Protocol):
    """Infrastructure port implemented by the worker's rag-helper runtime."""

    def execute(
        self,
        job: Mapping[str, Any],
        *,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> Mapping[str, Any]: ...


class KnowledgeIndexPayloadLoaderPort(Protocol):
    def load(self, reference: Mapping[str, Any]) -> bytes: ...


class KnowledgeIndexArtifactPublisherPort(Protocol):
    def publish(
        self,
        *,
        job_id: str,
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
        revision_binding: Mapping[str, Any] | None = None,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> list[dict[str, Any]]: ...


class KnowledgeIndexGraphArtifactMaterializerPort(Protocol):
    """Worker execution seam for graph artifacts derived from index outputs."""

    def materialize(
        self,
        *,
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
        revision_binding: Mapping[str, Any] | None = None,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> Mapping[str, Any]: ...


class KnowledgeIndexWorkerDispatchAdmissionPort(Protocol):
    """Worker-local preparation and atomic v2 execution claim."""

    def prepare(
        self,
        *,
        task: Mapping[str, Any],
        job: Mapping[str, Any],
        request_data: Any,
        expected_phase: str,
    ) -> Any: ...

    def claim_execute(
        self,
        *,
        task_id: str,
        prepared: Any,
    ) -> Any: ...

    def complete_execute_result(
        self,
        *,
        claimed: Any,
        result_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


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


class RagHelperKnowledgeIndexExecution:
    """Adapt the worker-local rag-helper service to the narrow execution port."""

    def __init__(
        self,
        index_service: Any,
        *,
        payload_loader: KnowledgeIndexPayloadLoaderPort | None = None,
        artifact_publisher: KnowledgeIndexArtifactPublisherPort | None = None,
        graph_artifact_materializer: KnowledgeIndexGraphArtifactMaterializerPort | None = None,
    ) -> None:
        self._index_service = index_service
        self._payload_loader = payload_loader
        self._artifact_publisher = artifact_publisher
        self._graph_artifact_materializer = graph_artifact_materializer

    def execute(
        self,
        job: Mapping[str, Any],
        *,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> Mapping[str, Any]:
        self._checkpoint(execution_deadline)
        payload = self._resolve_payload(
            job,
            execution_deadline=execution_deadline,
        )
        self._checkpoint(execution_deadline)
        deadline_kwargs = (
            {"execution_deadline": execution_deadline}
            if execution_deadline is not None
            else {}
        )
        job_type = str(job.get("job_type") or "")
        if job_type == "artifact":
            knowledge_index, run = self._index_service.index_artifact(
                str(payload.get("artifact_id") or ""),
                created_by=self._created_by(job),
                profile_name=self._profile_name(job),
                profile_overrides=dict(payload.get("profile_overrides") or {}),
                **deadline_kwargs,
            )
            self._checkpoint(execution_deadline)
            return self._single_result(
                job,
                payload,
                knowledge_index,
                run,
                execution_deadline=execution_deadline,
            )
        if job_type == "collection":
            results: list[dict[str, Any]] = []
            artifact_refs: list[dict[str, Any]] = []
            overall_status = "completed"
            for artifact_id in list(payload.get("artifact_ids") or []):
                self._checkpoint(execution_deadline)
                knowledge_index, run = self._index_service.index_artifact(
                    str(artifact_id),
                    created_by=self._created_by(job),
                    profile_name=self._profile_name(job),
                    profile_overrides=dict(payload.get("profile_overrides") or {}),
                    **deadline_kwargs,
                )
                self._checkpoint(execution_deadline)
                index_payload = self._model_dump(knowledge_index)
                run_payload = self._model_dump(run)
                results.append(
                    {
                        "artifact_id": str(artifact_id),
                        "knowledge_index": index_payload,
                        "run": run_payload,
                    }
                )
                artifact_refs.extend(
                    self._publish_outputs(
                        job=job,
                        payload=payload,
                        knowledge_index=index_payload,
                        run=run_payload,
                        execution_deadline=execution_deadline,
                    )
                )
                if self._is_failed(index_payload, run_payload):
                    overall_status = "failed"
            return {
                "status": overall_status,
                "results": results,
                "artifact_refs": artifact_refs,
                "reason_code": "knowledge_index_run_failed" if overall_status == "failed" else None,
            }
        if job_type == "source_records":
            knowledge_index, run = self._index_service.index_source_records(
                source_scope=str(payload.get("source_scope") or ""),
                source_id=str(payload.get("source_id") or ""),
                records=[dict(item) for item in list(payload.get("records") or [])],
                created_by=self._created_by(job),
                profile_name=self._profile_name(job),
                source_metadata=dict(payload.get("source_metadata") or {}),
                codecompass_prerender=bool(payload.get("codecompass_prerender", False)),
                **(
                    {"persist_control_plane_records": False}
                    if str(job.get("schema") or "") == BOUND_JOB_SCHEMA
                    else {}
                ),
                **deadline_kwargs,
            )
            self._checkpoint(execution_deadline)
            return self._single_result(
                job,
                payload,
                knowledge_index,
                run,
                execution_deadline=execution_deadline,
            )
        raise ValueError("knowledge_index_job_type_invalid")

    @staticmethod
    def _checkpoint(
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None,
    ) -> None:
        if execution_deadline is not None:
            execution_deadline.checkpoint()

    def _resolve_payload(
        self,
        job: Mapping[str, Any],
        *,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> dict[str, Any]:
        payload = dict(job.get("payload") or {})
        raw_reference = payload.get("payload_artifact_ref")
        if raw_reference is None:
            return payload
        if not isinstance(raw_reference, Mapping) or set(raw_reference) != {
            "artifact_id",
            "sha256",
            "size_bytes",
            "media_type",
            "encoding",
        }:
            raise ValueError("knowledge_index_payload_artifact_ref_invalid")
        reference = dict(raw_reference)
        if str(reference.get("encoding") or "") != "json":
            raise ValueError("knowledge_index_payload_artifact_encoding_invalid")
        if str(reference.get("media_type") or "").lower() != PAYLOAD_MEDIA_TYPE:
            raise ValueError("knowledge_index_payload_artifact_media_type_invalid")
        size_bytes = int(reference.get("size_bytes") or -1)
        if size_bytes < 0 or size_bytes > MAX_PAYLOAD_BYTES:
            raise ValueError("knowledge_index_payload_artifact_size_invalid")
        loader = self._payload_loader or HubArtifactKnowledgeIndexPayloadLoader()
        authorized_load = getattr(loader, "load_authorized", None)
        source_access_manifest = job.get(SOURCE_ACCESS_MANIFEST_FIELD)
        if str(job.get("schema") or "") == BOUND_JOB_SCHEMA:
            if not isinstance(source_access_manifest, Mapping):
                raise ValueError(
                    "knowledge_index_payload_capability_required"
                )
            if not callable(authorized_load):
                raise ValueError(
                    "knowledge_index_authorized_payload_loader_required"
                )
            authorized_load_kwargs: dict[str, Any] = {
                "source_access_manifest": source_access_manifest,
            }
            if execution_deadline is not None:
                authorized_load_kwargs["execution_deadline"] = (
                    execution_deadline
                )
            content = authorized_load(reference, **authorized_load_kwargs)
        else:
            content = loader.load(reference)
        if len(content) != size_bytes:
            raise ValueError("knowledge_index_payload_artifact_size_mismatch")
        if hashlib.sha256(content).hexdigest() != str(reference.get("sha256") or ""):
            raise ValueError("knowledge_index_payload_artifact_digest_mismatch")
        try:
            decoded = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("knowledge_index_payload_artifact_json_invalid") from exc
        if not isinstance(decoded, dict) or "payload_artifact_ref" in decoded:
            raise ValueError("knowledge_index_payload_artifact_payload_invalid")
        return dict(decoded)

    @staticmethod
    def _created_by(job: Mapping[str, Any]) -> str | None:
        value = str(job.get("created_by") or "").strip()
        return value or None

    @staticmethod
    def _profile_name(job: Mapping[str, Any]) -> str | None:
        value = str(job.get("profile_name") or "").strip()
        return value or None

    @staticmethod
    def _model_dump(value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"):
            payload = value.model_dump()
        elif isinstance(value, Mapping):
            payload = dict(value)
        else:
            raise TypeError("knowledge_index_worker_model_invalid")
        return dict(payload)

    @staticmethod
    def _is_failed(knowledge_index: Mapping[str, Any], run: Mapping[str, Any]) -> bool:
        return any(
            str(value or "").strip().lower() == "failed"
            for value in (knowledge_index.get("status"), run.get("status"))
        )

    def _single_result(
        self,
        job: Mapping[str, Any],
        payload: Mapping[str, Any],
        knowledge_index: Any,
        run: Any,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> dict[str, Any]:
        index_payload = self._model_dump(knowledge_index)
        run_payload = self._model_dump(run)
        failed = self._is_failed(index_payload, run_payload)
        return {
            "status": "failed" if failed else "completed",
            "reason_code": "knowledge_index_run_failed" if failed else None,
            "knowledge_index": index_payload,
            "run": run_payload,
            "artifact_refs": self._publish_outputs(
                job=job,
                payload=payload,
                knowledge_index=index_payload,
                run=run_payload,
                execution_deadline=execution_deadline,
            ),
            "error": str(run_payload.get("error_message") or "") or None,
        }

    def _publish_outputs(
        self,
        *,
        job: Mapping[str, Any],
        payload: Mapping[str, Any],
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> list[dict[str, Any]]:
        self._checkpoint(execution_deadline)
        if self._is_failed(knowledge_index, run):
            return []
        # A custom publisher without a graph materializer is the intentional
        # compatibility adapter for pre-graph integrations. Production uses the
        # defaults and therefore always requires both graph artifacts.
        graph_artifacts_required = (
            self._artifact_publisher is None or self._graph_artifact_materializer is not None
        )
        revision_binding = self._revision_binding(
            job=job,
            knowledge_index=knowledge_index,
        )
        domain_supplement_required = False
        if graph_artifacts_required:
            materializer = self._graph_artifact_materializer
            if materializer is None:
                from worker.retrieval.codecompass_graph_artifact_materializer import (
                    WorkerCodeCompassGraphArtifactMaterializer,
                )

                materializer = WorkerCodeCompassGraphArtifactMaterializer()
            raw_options = payload.get("graph_visual_metrics")
            if raw_options is not None and not isinstance(raw_options, Mapping):
                raise ValueError("graph_visual_options_invalid")
            materializer_kwargs = (
                {"execution_deadline": execution_deadline}
                if execution_deadline is not None
                else {}
            )
            if revision_binding is not None and self._graph_artifact_materializer is None:
                materializer_kwargs["revision_binding"] = revision_binding
            materialization = materializer.materialize(
                knowledge_index=knowledge_index,
                run=run,
                options=raw_options,
                **materializer_kwargs,
            )
            domain_supplement_required = bool(
                isinstance(materialization, Mapping)
                and isinstance(
                    materialization.get("domain_supplement"), Mapping
                )
            )
            self._checkpoint(execution_deadline)

        publisher = self._artifact_publisher or WorkerKnowledgeIndexArtifactPublisher()
        publisher_kwargs = (
            {"execution_deadline": execution_deadline}
            if execution_deadline is not None
            else {}
        )
        if revision_binding is not None and self._artifact_publisher is None:
            publisher_kwargs["revision_binding"] = revision_binding
        references = publisher.publish(
            job_id=str(job.get("job_id") or ""),
            knowledge_index=knowledge_index,
            run=run,
            **publisher_kwargs,
        )
        self._checkpoint(execution_deadline)
        roles = {str(item.get("role") or "") for item in references}
        required_roles = {"manifest", "index"}
        if graph_artifacts_required:
            required_roles.update({"graph_index", "graph_visual_metrics"})
        if domain_supplement_required:
            required_roles.add(DOMAIN_SUPPLEMENT_OUTPUT_ROLE)
        if not required_roles.issubset(roles):
            raise RuntimeError("knowledge_index_output_artifacts_incomplete")
        return references

    @staticmethod
    def _revision_binding(
        *,
        job: Mapping[str, Any],
        knowledge_index: Mapping[str, Any],
    ) -> dict[str, str] | None:
        if str(job.get("schema") or "") != BOUND_JOB_SCHEMA:
            return None
        authority = job.get("authority_binding")
        metadata = knowledge_index.get("index_metadata")
        if not isinstance(authority, Mapping) or not isinstance(metadata, Mapping):
            raise ValueError("knowledge_index_revision_binding_missing")
        return {
            "source_scope": str(metadata.get("source_scope") or "").strip(),
            "source_id": str(metadata.get("source_id") or "").strip(),
            "source_revision_id": str(
                authority.get("source_revision_id") or ""
            ).strip(),
            "source_revision_digest": str(
                authority.get("source_revision_digest") or ""
            ).strip(),
        }


class WorkerKnowledgeIndexArtifactPublisher:
    """Publish real worker output files through the existing artifact API."""

    _OUTPUTS = {
        "manifest": ("manifest.json", "application/json"),
        "index": ("index.jsonl", "application/x-ndjson"),
        "details": ("details.jsonl", "application/x-ndjson"),
        "relations": ("relations.jsonl", "application/x-ndjson"),
        "graph_index": (
            "cc_graph_index.json",
            "application/vnd.ananta.codecompass-graph-index+json",
        ),
        "graph_visual_metrics": (
            "cc_graph_index.visual_metrics.json",
            "application/vnd.ananta.codecompass-graph-visual-metrics+json",
        ),
        DOMAIN_SUPPLEMENT_OUTPUT_ROLE: (
            DOMAIN_SUPPLEMENT_FILENAME,
            DOMAIN_SUPPLEMENT_MEDIA_TYPE,
        ),
    }
    _MAX_OUTPUT_BYTES = 128 * 1024 * 1024
    _MAX_GRAPH_OUTPUT_BYTES = MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES
    _GRAPH_OUTPUT_ROLES = frozenset({"graph_index", "graph_visual_metrics"})
    _REVISION_BOUND_OUTPUT_ROLES = _GRAPH_OUTPUT_ROLES | frozenset(
        {DOMAIN_SUPPLEMENT_OUTPUT_ROLE}
    )
    _READ_CHUNK_BYTES = 1024 * 1024

    def publish(
        self,
        *,
        job_id: str,
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
        revision_binding: Mapping[str, Any] | None = None,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> list[dict[str, Any]]:
        from agent.repository import artifact_repo
        from agent.services.ingestion_service import get_ingestion_service

        self._checkpoint(execution_deadline)
        output_dir_value = str(run.get("output_dir") or knowledge_index.get("output_dir") or "").strip()
        if not output_dir_value:
            raise RuntimeError("knowledge_index_output_directory_missing")
        output_dir = Path(output_dir_value)
        if output_dir.is_symlink():
            raise RuntimeError("knowledge_index_output_directory_invalid")
        try:
            resolved_output = output_dir.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("knowledge_index_output_directory_missing") from exc
        if not resolved_output.is_dir() or resolved_output.is_symlink():
            raise RuntimeError("knowledge_index_output_directory_invalid")
        graph_files = (
            resolved_output / "cc_graph_index.json",
            resolved_output / "cc_graph_index.visual_metrics.json",
        )
        graph_file_presence = tuple(path.exists() for path in graph_files)
        supplement_path = resolved_output / DOMAIN_SUPPLEMENT_FILENAME
        supplement_present = supplement_path.exists()
        if supplement_present and revision_binding is None:
            raise RuntimeError("knowledge_index_revision_binding_missing")
        if any(graph_file_presence) and not all(graph_file_presence):
            raise RuntimeError("knowledge_index_graph_artifacts_incomplete")
        if supplement_present and not all(graph_file_presence):
            raise RuntimeError("knowledge_index_graph_artifacts_incomplete")
        if all(graph_file_presence):
            for graph_file in graph_files:
                if graph_file.is_symlink() or not graph_file.is_file():
                    raise RuntimeError("knowledge_index_output_artifact_invalid")
                if graph_file.stat().st_size > self._MAX_GRAPH_OUTPUT_BYTES:
                    raise RuntimeError("knowledge_index_graph_artifact_too_large")
            graph_binding = self._load_graph_binding(
                resolved_output,
                knowledge_index=knowledge_index,
                revision_binding=revision_binding,
                include_domain_supplement=supplement_present,
                execution_deadline=execution_deadline,
            )
        else:
            graph_binding = {}
        references: list[dict[str, Any]] = []
        for role, (filename, media_type) in self._OUTPUTS.items():
            self._checkpoint(execution_deadline)
            if role == DOMAIN_SUPPLEMENT_OUTPUT_ROLE and not supplement_present:
                continue
            path = resolved_output / filename
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("knowledge_index_output_artifact_invalid")
            size_bytes = path.stat().st_size
            if (
                role in self._GRAPH_OUTPUT_ROLES
                and size_bytes > self._MAX_GRAPH_OUTPUT_BYTES
            ):
                raise RuntimeError("knowledge_index_graph_artifact_too_large")
            if size_bytes < 0 or size_bytes > self._MAX_OUTPUT_BYTES:
                raise RuntimeError("knowledge_index_output_artifact_too_large")
            content = self._read_bounded_file(
                path,
                expected_size=size_bytes,
                execution_deadline=execution_deadline,
            )
            if len(content) != size_bytes:
                raise RuntimeError("knowledge_index_output_artifact_size_mismatch")
            upload_kwargs = (
                {"execution_checkpoint": execution_deadline.checkpoint}
                if execution_deadline is not None
                else {}
            )
            initial_artifact_metadata = {
                "system_artifact_kind": "knowledge_index_worker_output",
                "knowledge_index_job_id": job_id,
                "knowledge_index_id": str(knowledge_index.get("id") or ""),
                "knowledge_index_run_id": str(run.get("id") or ""),
                "output_role": role,
                **(
                    graph_binding.get(role, {})
                    if role in self._REVISION_BOUND_OUTPUT_ROLES
                    else {}
                ),
            }
            artifact, version, _collection = get_ingestion_service().upload_artifact(
                filename=f"{job_id}-{run.get('id')}-{filename}",
                content=content,
                created_by="knowledge-index-worker",
                media_type=media_type,
                artifact_metadata=initial_artifact_metadata,
                **upload_kwargs,
            )
            self._checkpoint(execution_deadline)
            artifact.artifact_metadata = {
                **dict(artifact.artifact_metadata or {}),
                **initial_artifact_metadata,
            }
            reference = {
                "artifact_id": artifact.id,
                "sha256": version.sha256,
                "media_type": version.media_type,
                "role": role,
                "filename": filename,
                "size_bytes": version.size_bytes,
                "knowledge_index_id": str(knowledge_index.get("id") or ""),
                "run_id": str(run.get("id") or ""),
            }
            if role in self._REVISION_BOUND_OUTPUT_ROLES:
                if role not in graph_binding:
                    raise RuntimeError("knowledge_index_graph_artifacts_incomplete")
                reference.update(graph_binding[role])
                artifact.artifact_metadata = {
                    **dict(artifact.artifact_metadata or {}),
                    **graph_binding[role],
                }
            artifact_repo.save(artifact)
            references.append(reference)
        self._checkpoint(execution_deadline)
        return references

    @classmethod
    def _load_graph_binding(
        cls,
        output_dir: Path,
        *,
        knowledge_index: Mapping[str, Any],
        revision_binding: Mapping[str, Any] | None,
        include_domain_supplement: bool,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None,
    ) -> dict[str, dict[str, Any]]:
        graph_path = output_dir / "cc_graph_index.json"
        metrics_path = output_dir / "cc_graph_index.visual_metrics.json"
        try:
            graph_bytes = cls._read_bounded_file(
                graph_path,
                expected_size=graph_path.stat().st_size,
                execution_deadline=execution_deadline,
            )
            metrics_bytes = cls._read_bounded_file(
                metrics_path,
                expected_size=metrics_path.stat().st_size,
                execution_deadline=execution_deadline,
            )
            graph = json.loads(graph_bytes)
            metrics = json.loads(metrics_bytes)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("knowledge_index_graph_artifacts_invalid") from exc
        if not isinstance(graph, dict) or not isinstance(metrics, dict):
            raise RuntimeError("knowledge_index_graph_artifacts_invalid")
        state = graph.get("state")
        if not isinstance(state, Mapping):
            raise RuntimeError("knowledge_index_graph_artifact_revision_mismatch")
        graph_revision = str(state.get("manifest_hash") or "").strip()
        if (
            str(state.get("schema") or "") != "codecompass_graph_index.v1"
            or str(metrics.get("schema") or "") != "graph_visual_metrics.v1"
            or str(metrics.get("graph_revision") or "") != graph_revision
            or not graph_revision
            or not graph_revision.startswith("sha256:")
            or len(graph_revision) != 71
        ):
            raise RuntimeError("knowledge_index_graph_artifact_revision_mismatch")
        from worker.retrieval.codecompass_graph_visual_metrics import (
            verify_visual_metrics_content_hash,
        )

        if not verify_visual_metrics_content_hash(metrics):
            raise RuntimeError("knowledge_index_graph_visual_metrics_hash_invalid")
        binding: dict[str, dict[str, Any]] = {
            "graph_index": {
                "artifact_schema": "codecompass_graph_index.v1",
                "graph_revision": graph_revision,
                "graph_content_hash": "sha256:"
                + hashlib.sha256(graph_bytes).hexdigest(),
            },
            "graph_visual_metrics": {
                "artifact_schema": "graph_visual_metrics.v1",
                "graph_revision": graph_revision,
                "graph_content_hash": str(metrics.get("content_hash") or ""),
            },
        }
        if include_domain_supplement:
            if revision_binding is None:
                raise RuntimeError("knowledge_index_revision_binding_missing")
            from worker.retrieval.codecompass_domain_supplement import (
                WorkerCodeCompassDomainSupplementMaterializer,
            )

            try:
                supplement = (
                    WorkerCodeCompassDomainSupplementMaterializer.inspect_published(
                        output_dir / DOMAIN_SUPPLEMENT_FILENAME,
                        execution_deadline=execution_deadline,
                    )
                )
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    "knowledge_index_domain_supplement_invalid"
                ) from exc
            expected_supplement = {
                "graph_revision": graph_revision,
                "knowledge_index_id": str(knowledge_index.get("id") or ""),
                "source_scope": str(
                    revision_binding.get("source_scope") or ""
                ),
                "source_id": str(revision_binding.get("source_id") or ""),
                "source_revision_id": str(
                    revision_binding.get("source_revision_id") or ""
                ),
                "source_revision_digest": str(
                    revision_binding.get("source_revision_digest") or ""
                ),
            }
            if any(
                str(supplement.get(field) or "") != expected
                for field, expected in expected_supplement.items()
            ):
                raise RuntimeError(
                    "knowledge_index_domain_supplement_binding_mismatch"
                )
            binding[DOMAIN_SUPPLEMENT_OUTPUT_ROLE] = {
                key: value
                for key, value in supplement.items()
                if key
                in {
                    "artifact_schema",
                    "graph_revision",
                    "graph_content_hash",
                    "source_revision_id",
                    "source_revision_digest",
                    "domain_count",
                    "semantic_node_count",
                    "semantic_edge_count",
                    "declaration_edge_count",
                }
            }
        return binding

    @staticmethod
    def _checkpoint(
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None,
    ) -> None:
        if execution_deadline is not None:
            execution_deadline.checkpoint()

    @classmethod
    def _read_bounded_file(
        cls,
        path: Path,
        *,
        expected_size: int,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None,
    ) -> bytes:
        chunks: list[bytes] = []
        received = 0
        with path.open("rb") as handle:
            while True:
                cls._checkpoint(execution_deadline)
                chunk = handle.read(cls._READ_CHUNK_BYTES)
                cls._checkpoint(execution_deadline)
                if not chunk:
                    break
                received += len(chunk)
                if received > expected_size:
                    raise RuntimeError(
                        "knowledge_index_output_artifact_size_mismatch"
                    )
                chunks.append(chunk)
        if received != expected_size:
            raise RuntimeError(
                "knowledge_index_output_artifact_size_mismatch"
            )
        return b"".join(chunks)


class HubArtifactKnowledgeIndexPayloadLoader:
    """Fetch a Hub-owned payload artifact with strict size and digest bounds."""

    def load(self, reference: Mapping[str, Any]) -> bytes:
        artifact_id = str(reference.get("artifact_id") or "").strip()
        expected_size = int(reference.get("size_bytes") or -1)
        if not artifact_id or expected_size < 0 or expected_size > MAX_PAYLOAD_BYTES:
            raise ValueError("knowledge_index_payload_artifact_ref_invalid")
        local = self._load_local(artifact_id, expected_size=expected_size)
        if local is not None:
            return local
        return self._load_from_hub(
            artifact_id,
            expected_size=expected_size,
            expected_sha256=str(reference.get("sha256") or "").lower(),
        )

    def load_authorized(
        self,
        reference: Mapping[str, Any],
        *,
        source_access_manifest: Mapping[str, Any],
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> bytes:
        artifact_id = str(reference.get("artifact_id") or "").strip()
        expected_size = int(reference.get("size_bytes") or -1)
        if not artifact_id or expected_size < 0 or expected_size > MAX_PAYLOAD_BYTES:
            raise ValueError("knowledge_index_payload_artifact_ref_invalid")
        if not isinstance(source_access_manifest, Mapping):
            raise ValueError(
                "knowledge_index_payload_capability_required"
            )
        # Governed reads always return through the Hub so the signed,
        # assignment-bound capability is revalidated against live authority.
        # A shared filesystem must never bypass that control-plane decision.
        hub_load_kwargs: dict[str, Any] = {
            "expected_size": expected_size,
            "expected_sha256": str(reference.get("sha256") or "").lower(),
            "source_access_manifest": source_access_manifest,
        }
        if execution_deadline is not None:
            hub_load_kwargs["execution_deadline"] = execution_deadline
        return self._load_from_hub(artifact_id, **hub_load_kwargs)

    @staticmethod
    def _load_local(artifact_id: str, *, expected_size: int) -> bytes | None:
        try:
            from agent.repository import artifact_version_repo

            versions = artifact_version_repo.get_by_artifact(artifact_id)
            if not versions:
                return None
            path = Path(str(versions[0].storage_path)).resolve(strict=True)
            if not path.is_file() or path.stat().st_size != expected_size:
                return None
            return path.read_bytes()
        except (OSError, ValueError):
            return None

    @staticmethod
    def _load_from_hub(
        artifact_id: str,
        *,
        expected_size: int,
        expected_sha256: str,
        source_access_manifest: Mapping[str, Any] | None = None,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> bytes:
        from agent.auth import resolve_configured_agent_token
        from agent.config import settings
        from worker.runtime.workflow_service_identity import WorkflowServiceIdentity

        hub_url = str(settings.hub_url or "").strip().rstrip("/")
        if not hub_url.startswith(("http://", "https://")):
            raise ValueError("knowledge_index_payload_hub_url_invalid")
        token = resolve_configured_agent_token(
            {
                "AGENT_TOKEN": settings.agent_token,
                "AGENT_TOKEN_FILE": settings.agent_token_file,
            }
        )
        if source_access_manifest is not None and not token:
            raise ValueError(
                "knowledge_index_payload_worker_service_token_required"
            )
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        identity = WorkflowServiceIdentity.optional(
            worker_id=settings.agent_name,
            worker_url=str(settings.agent_url or ""),
        )
        if identity is not None:
            headers.update(identity.headers())
        if source_access_manifest is not None:
            from ananta_contracts.knowledge_index_payload_capability import (
                KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER,
                encode_knowledge_index_payload_capability,
            )

            headers[KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER] = (
                encode_knowledge_index_payload_capability(
                    source_access_manifest
                )
            )
        encoded_id = urllib.parse.quote(artifact_id, safe="")
        request = urllib.request.Request(
            f"{hub_url}/internal/knowledge-index/payload-artifacts/{encoded_id}",
            headers=headers,
            method="GET",
        )
        opener = urllib.request.build_opener(
            _KnowledgeIndexPayloadNoRedirectHandler()
        )
        timeout = 60.0
        if execution_deadline is not None:
            execution_deadline.checkpoint()
            timeout = min(
                timeout,
                execution_deadline.remaining_seconds(),
            )
        try:
            response = opener.open(request, timeout=timeout)
        except TimeoutError:
            if execution_deadline is not None:
                execution_deadline.checkpoint()
            raise
        except urllib.error.HTTPError as exc:
            if 300 <= int(exc.code or 0) < 400:
                exc.close()
                raise ValueError(
                    "knowledge_index_payload_redirect_forbidden"
                ) from exc
            raise
        status = getattr(response, "status", None)
        if status is None:
            getcode = getattr(response, "getcode", None)
            status = getcode() if callable(getcode) else None
        if status is not None and 300 <= int(status) < 400:
            response.close()
            raise ValueError(
                "knowledge_index_payload_redirect_forbidden"
            )
        with response:
            if execution_deadline is not None:
                execution_deadline.checkpoint()
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) != expected_size:
                raise ValueError("knowledge_index_payload_artifact_size_mismatch")
            declared_hash = str(response.headers.get("X-Artifact-SHA256") or "").lower()
            if declared_hash and declared_hash != expected_sha256:
                raise ValueError("knowledge_index_payload_artifact_digest_mismatch")
            chunks: list[bytes] = []
            received = 0
            while True:
                if declared is not None and received == expected_size:
                    break
                maximum = min(
                    _PAYLOAD_READ_CHUNK_BYTES,
                    expected_size - received + 1,
                )
                chunk = HubArtifactKnowledgeIndexPayloadLoader._read_chunk(
                    response,
                    maximum,
                    execution_deadline=execution_deadline,
                )
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > expected_size:
                    raise ValueError(
                        "knowledge_index_payload_artifact_size_mismatch"
                    )
            content = b"".join(chunks)
        if len(content) != expected_size:
            raise ValueError("knowledge_index_payload_artifact_size_mismatch")
        return content

    @staticmethod
    def _read_chunk(
        response: Any,
        maximum_bytes: int,
        *,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None,
    ) -> bytes:
        if maximum_bytes <= 0:
            return b""
        if execution_deadline is not None:
            execution_deadline.checkpoint()
            if not HubArtifactKnowledgeIndexPayloadLoader._set_socket_timeout(
                response,
                min(60.0, execution_deadline.remaining_seconds()),
            ):
                raise KnowledgeIndexExecutionDeadlineError(
                    "knowledge_index_worker_payload_deadline_transport_unsupported"
                )
        reader = getattr(response, "read1", None)
        if not callable(reader):
            reader = response.read
        try:
            chunk = reader(maximum_bytes)
        except TimeoutError:
            if execution_deadline is not None:
                execution_deadline.checkpoint()
            raise
        if execution_deadline is not None:
            execution_deadline.checkpoint()
        if not isinstance(chunk, bytes):
            raise ValueError("knowledge_index_payload_artifact_bytes_invalid")
        return chunk

    @staticmethod
    def _set_socket_timeout(response: Any, timeout: float) -> bool:
        fp = getattr(response, "fp", None)
        raw = getattr(fp, "raw", None)
        for candidate in (getattr(raw, "_sock", None), raw, fp):
            setter = getattr(candidate, "settimeout", None)
            if callable(setter):
                setter(timeout)
                return True
        return False


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
