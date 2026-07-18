"""Persistent Hub-owned orchestration for delegated knowledge-index jobs."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any, Protocol

KNOWLEDGE_INDEX_JOB_SCHEMA = "ananta.knowledge_index_job.v1"
KNOWLEDGE_INDEX_RESULT_SCHEMA = "ananta.knowledge_index_job_result.v1"
_INLINE_JOB_PAYLOAD_BYTES = 128 * 1024
_MAX_JOB_PAYLOAD_BYTES = 128 * 1024 * 1024
_PAYLOAD_MEDIA_TYPE = "application/vnd.ananta.knowledge-index-job+json"
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


class KnowledgeIndexJobRepositoryPort(Protocol):
    def get_by_id(self, task_id: str) -> Any | None: ...


class KnowledgeIndexTaskQueuePort(Protocol):
    def ingest_task(self, **kwargs: Any) -> None: ...


class KnowledgeIndexPayloadStorePort(Protocol):
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

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        task = self._repository().get_by_id(str(job_id))
        if task is None:
            return None
        raw = task.model_dump() if hasattr(task, "model_dump") else dict(task)
        context = dict(raw.get("worker_execution_context") or {})
        envelope = dict(context.get("knowledge_index_job") or {})
        if str(envelope.get("schema") or "") != KNOWLEDGE_INDEX_JOB_SCHEMA:
            return None
        task_status = str(raw.get("status") or "todo").strip().lower()
        status = {
            "created": "queued",
            "todo": "queued",
            "blocked": "queued",
            "blocked_by_dependency": "queued",
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
        }
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
            },
        )

    def accept_worker_result(self, *, job_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and persist a worker result through the existing task state path."""

        payload = self.validate_worker_result(job_id=job_id, result=result)
        task = self._repository().get_by_id(str(job_id))
        raw_task = task.model_dump() if hasattr(task, "model_dump") else dict(task)
        status = str(payload.get("status") or "").strip().lower()
        envelope = dict((raw_task.get("worker_execution_context") or {}).get("knowledge_index_job") or {})
        from agent.services.task_runtime_service import update_local_task_status

        verification = dict(raw_task.get("verification_status") or {})
        verification["knowledge_index_job_result"] = payload
        update_local_task_status(
            str(job_id),
            status,
            status_reason_code=str(payload.get("reason_code") or "") or None,
            verification_status=verification,
            event_type=f"knowledge_index_job_{status}",
            event_actor="knowledge-index-worker-gateway",
            event_details={
                "idempotency_fingerprint": envelope.get("idempotency_fingerprint"),
                "worker_result_schema": KNOWLEDGE_INDEX_RESULT_SCHEMA,
            },
        )
        return self.get_job(job_id) or {}

    def materialize_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
        task: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate and admit worker artifacts before a Hub task can complete."""

        payload = self.validate_worker_result(job_id=job_id, result=result)
        service = self._worker_artifact_service
        if service is None:
            from agent.services.knowledge_index_worker_artifact_service import (
                KnowledgeIndexWorkerArtifactService,
            )

            service = KnowledgeIndexWorkerArtifactService()
        return service.materialize(job_id=job_id, result=payload, task=task)

    def validate_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return a schema-shaped result only when it is bound to the Hub task."""

        task = self._repository().get_by_id(str(job_id))
        if task is None:
            raise ValueError("knowledge_index_job_not_found")
        raw_task = task.model_dump() if hasattr(task, "model_dump") else dict(task)
        envelope = dict((raw_task.get("worker_execution_context") or {}).get("knowledge_index_job") or {})
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
        required_reference_fields = {"artifact_id", "sha256", "media_type"}
        optional_reference_fields = {
            "role",
            "filename",
            "size_bytes",
            "knowledge_index_id",
            "run_id",
        }
        for reference in artifact_refs:
            if (
                not isinstance(reference, Mapping)
                or not required_reference_fields.issubset(reference)
                or set(reference) - required_reference_fields - optional_reference_fields
            ):
                raise ValueError("knowledge_index_result_artifact_ref_invalid")
            artifact_id = str(reference.get("artifact_id") or "").strip()
            sha256 = str(reference.get("sha256") or "")
            media_type = str(reference.get("media_type") or "").strip()
            if not artifact_id or not media_type:
                raise ValueError("knowledge_index_result_artifact_ref_invalid")
            if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
                raise ValueError("knowledge_index_result_artifact_ref_digest_invalid")
            if any(field in reference for field in optional_reference_fields):
                if not optional_reference_fields.issubset(reference):
                    raise ValueError("knowledge_index_result_artifact_ref_incomplete")
                if str(reference.get("role") or "") not in {
                    "manifest",
                    "index",
                    "details",
                    "relations",
                }:
                    raise ValueError("knowledge_index_result_artifact_ref_role_invalid")
                if str(reference.get("filename") or "") not in {
                    "manifest.json",
                    "index.jsonl",
                    "details.jsonl",
                    "relations.jsonl",
                }:
                    raise ValueError("knowledge_index_result_artifact_ref_filename_invalid")
                size_bytes = int(reference.get("size_bytes") or 0)
                if size_bytes < 0 or size_bytes > 128 * 1024 * 1024:
                    raise ValueError("knowledge_index_result_artifact_ref_size_invalid")
                if not str(reference.get("knowledge_index_id") or "").strip():
                    raise ValueError("knowledge_index_result_artifact_ref_index_id_invalid")
                if not str(reference.get("run_id") or "").strip():
                    raise ValueError("knowledge_index_result_artifact_ref_run_id_invalid")
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
            payload_artifact_ref = self._store_large_payload(
                content=payload_bytes,
                fingerprint=idempotency_fingerprint,
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
    "KnowledgeIndexJobService",
    "get_knowledge_index_job_service",
]
