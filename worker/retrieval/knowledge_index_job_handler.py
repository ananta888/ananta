"""Worker-side execution boundary for Hub-delegated knowledge-index jobs."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

JOB_SCHEMA = "ananta.knowledge_index_job.v1"
RESULT_SCHEMA = "ananta.knowledge_index_job_result.v1"
PAYLOAD_MEDIA_TYPE = "application/vnd.ananta.knowledge-index-job+json"
MAX_PAYLOAD_BYTES = 128 * 1024 * 1024


class KnowledgeIndexExecutionPort(Protocol):
    """Infrastructure port implemented by the worker's rag-helper runtime."""

    def execute(self, job: Mapping[str, Any]) -> Mapping[str, Any]: ...


class KnowledgeIndexPayloadLoaderPort(Protocol):
    def load(self, reference: Mapping[str, Any]) -> bytes: ...


class KnowledgeIndexArtifactPublisherPort(Protocol):
    def publish(
        self,
        *,
        job_id: str,
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> list[dict[str, Any]]: ...


class KnowledgeIndexWorkerTaskHandler:
    """Validate one Hub envelope, execute once, and return an immutable result."""

    def __init__(self, execution: KnowledgeIndexExecutionPort) -> None:
        self._execution = execution

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        """Expose a non-shell executable marker for the deterministic pipeline."""

        job = self._resolve_job(None, kwargs)
        self._validate_job(job)
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
        self._validate_job(job)
        try:
            raw_result = dict(self._execution.execute(job) or {})
        except Exception as exc:
            return self._result(
                job,
                status="failed",
                reason_code=f"worker_execution_failed:{type(exc).__name__}",
                error=str(exc)[:1000],
            )
        status = str(raw_result.get("status") or "").strip().lower()
        if status not in {"completed", "failed"}:
            return self._result(
                job,
                status="failed",
                reason_code="worker_result_status_invalid",
                error="execution port returned a non-terminal status",
            )
        return self._result(
            job,
            status=status,
            reason_code=str(raw_result.get("reason_code") or "") or None,
            knowledge_index=raw_result.get("knowledge_index"),
            run=raw_result.get("run"),
            results=raw_result.get("results"),
            artifact_refs=list(raw_result.get("artifact_refs") or []),
            error=str(raw_result.get("error") or "") or None,
        )

    @staticmethod
    def _resolve_job(
        envelope: Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
    ) -> dict[str, Any]:
        if isinstance(envelope, Mapping) and str(envelope.get("schema") or "") == JOB_SCHEMA:
            return dict(envelope)
        task = kwargs.get("task")
        if isinstance(task, Mapping):
            context = task.get("worker_execution_context")
            if isinstance(context, Mapping):
                job = context.get("knowledge_index_job")
                if isinstance(job, Mapping):
                    return dict(job)
        raise ValueError("knowledge_index_job_envelope_missing")

    @staticmethod
    def _validate_job(job: Mapping[str, Any]) -> None:
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
    ) -> None:
        self._index_service = index_service
        self._payload_loader = payload_loader
        self._artifact_publisher = artifact_publisher

    def execute(self, job: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = self._resolve_payload(job)
        job_type = str(job.get("job_type") or "")
        if job_type == "artifact":
            knowledge_index, run = self._index_service.index_artifact(
                str(payload.get("artifact_id") or ""),
                created_by=self._created_by(job),
                profile_name=self._profile_name(job),
                profile_overrides=dict(payload.get("profile_overrides") or {}),
            )
            return self._single_result(job, knowledge_index, run)
        if job_type == "collection":
            results: list[dict[str, Any]] = []
            artifact_refs: list[dict[str, Any]] = []
            overall_status = "completed"
            for artifact_id in list(payload.get("artifact_ids") or []):
                knowledge_index, run = self._index_service.index_artifact(
                    str(artifact_id),
                    created_by=self._created_by(job),
                    profile_name=self._profile_name(job),
                    profile_overrides=dict(payload.get("profile_overrides") or {}),
                )
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
                        knowledge_index=index_payload,
                        run=run_payload,
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
            )
            return self._single_result(job, knowledge_index, run)
        raise ValueError("knowledge_index_job_type_invalid")

    def _resolve_payload(self, job: Mapping[str, Any]) -> dict[str, Any]:
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
        knowledge_index: Any,
        run: Any,
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
                knowledge_index=index_payload,
                run=run_payload,
            ),
            "error": str(run_payload.get("error_message") or "") or None,
        }

    def _publish_outputs(
        self,
        *,
        job: Mapping[str, Any],
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if self._is_failed(knowledge_index, run):
            return []
        publisher = self._artifact_publisher or WorkerKnowledgeIndexArtifactPublisher()
        references = publisher.publish(
            job_id=str(job.get("job_id") or ""),
            knowledge_index=knowledge_index,
            run=run,
        )
        roles = {str(item.get("role") or "") for item in references}
        if not {"manifest", "index"}.issubset(roles):
            raise RuntimeError("knowledge_index_output_artifacts_incomplete")
        return references


class WorkerKnowledgeIndexArtifactPublisher:
    """Publish real worker output files through the existing artifact API."""

    _OUTPUTS = {
        "manifest": ("manifest.json", "application/json"),
        "index": ("index.jsonl", "application/x-ndjson"),
        "details": ("details.jsonl", "application/x-ndjson"),
        "relations": ("relations.jsonl", "application/x-ndjson"),
    }
    _MAX_OUTPUT_BYTES = 128 * 1024 * 1024

    def publish(
        self,
        *,
        job_id: str,
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        from agent.repository import artifact_repo
        from agent.services.ingestion_service import get_ingestion_service

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
        references: list[dict[str, Any]] = []
        for role, (filename, media_type) in self._OUTPUTS.items():
            path = resolved_output / filename
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("knowledge_index_output_artifact_invalid")
            size_bytes = path.stat().st_size
            if size_bytes < 0 or size_bytes > self._MAX_OUTPUT_BYTES:
                raise RuntimeError("knowledge_index_output_artifact_too_large")
            content = path.read_bytes()
            if len(content) != size_bytes:
                raise RuntimeError("knowledge_index_output_artifact_size_mismatch")
            artifact, version, _collection = get_ingestion_service().upload_artifact(
                filename=f"{job_id}-{run.get('id')}-{filename}",
                content=content,
                created_by="knowledge-index-worker",
                media_type=media_type,
            )
            artifact.artifact_metadata = {
                **dict(artifact.artifact_metadata or {}),
                "system_artifact_kind": "knowledge_index_worker_output",
                "knowledge_index_job_id": job_id,
                "knowledge_index_id": str(knowledge_index.get("id") or ""),
                "knowledge_index_run_id": str(run.get("id") or ""),
                "output_role": role,
            }
            artifact_repo.save(artifact)
            references.append(
                {
                    "artifact_id": artifact.id,
                    "sha256": version.sha256,
                    "media_type": version.media_type,
                    "role": role,
                    "filename": filename,
                    "size_bytes": version.size_bytes,
                    "knowledge_index_id": str(knowledge_index.get("id") or ""),
                    "run_id": str(run.get("id") or ""),
                }
            )
        return references


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
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        identity = WorkflowServiceIdentity.optional(
            worker_id=settings.agent_name,
            worker_url=str(settings.agent_url or ""),
        )
        if identity is not None:
            headers.update(identity.headers())
        encoded_id = urllib.parse.quote(artifact_id, safe="")
        request = urllib.request.Request(
            f"{hub_url}/internal/knowledge-index/payload-artifacts/{encoded_id}",
            headers=headers,
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) != expected_size:
                raise ValueError("knowledge_index_payload_artifact_size_mismatch")
            declared_hash = str(response.headers.get("X-Artifact-SHA256") or "").lower()
            if declared_hash and declared_hash != expected_sha256:
                raise ValueError("knowledge_index_payload_artifact_digest_mismatch")
            content = response.read(min(MAX_PAYLOAD_BYTES, expected_size) + 1)
        if len(content) != expected_size:
            raise ValueError("knowledge_index_payload_artifact_size_mismatch")
        return content


def build_knowledge_index_task_handler(
    index_service: Any | None = None,
    *,
    payload_loader: KnowledgeIndexPayloadLoaderPort | None = None,
    artifact_publisher: KnowledgeIndexArtifactPublisherPort | None = None,
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
        )
    )


__all__ = [
    "KnowledgeIndexExecutionPort",
    "KnowledgeIndexPayloadLoaderPort",
    "KnowledgeIndexArtifactPublisherPort",
    "KnowledgeIndexWorkerTaskHandler",
    "HubArtifactKnowledgeIndexPayloadLoader",
    "WorkerKnowledgeIndexArtifactPublisher",
    "RagHelperKnowledgeIndexExecution",
    "build_knowledge_index_task_handler",
]
