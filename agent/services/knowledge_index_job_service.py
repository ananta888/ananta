from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent.metrics import KNOWLEDGE_INDEX_ACTIVE_JOBS
from agent.services.rag_helper_index_service import get_rag_helper_index_service


class KnowledgeIndexJobService:
    """Runs longer knowledge-index tasks off the request thread with observable status."""

    def __init__(self, index_service=None, *, max_workers: int = 2) -> None:
        self._index_service = index_service or get_rag_helper_index_service()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="knowledge-index")
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def _save_job(self, job: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._jobs[str(job["job_id"])] = job
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _run_artifact_job(
        self,
        *,
        job_id: str,
        artifact_id: str,
        created_by: str | None,
        profile_name: str | None,
        profile_overrides: dict[str, Any] | None,
    ) -> None:
        KNOWLEDGE_INDEX_ACTIVE_JOBS.inc()
        started_at = time.time()
        self._save_job(
            {
                "job_id": job_id,
                "job_type": "artifact",
                "scope_id": artifact_id,
                "status": "running",
                "created_by": created_by,
                "profile_name": profile_name or "default",
                "created_at": started_at,
                "started_at": started_at,
            }
        )
        try:
            knowledge_index, run = self._index_service.index_artifact(
                artifact_id,
                created_by=created_by,
                profile_name=profile_name,
                profile_overrides=profile_overrides,
            )
            self._save_job(
                {
                    "job_id": job_id,
                    "job_type": "artifact",
                    "scope_id": artifact_id,
                    "status": "completed" if str(getattr(run, "status", "")) == "completed" else "failed",
                    "created_by": created_by,
                    "profile_name": profile_name or "default",
                    "created_at": started_at,
                    "started_at": started_at,
                    "finished_at": time.time(),
                    "knowledge_index": knowledge_index.model_dump(),
                    "run": run.model_dump(),
                }
            )
        except Exception as exc:
            self._save_job(
                {
                    "job_id": job_id,
                    "job_type": "artifact",
                    "scope_id": artifact_id,
                    "status": "failed",
                    "created_by": created_by,
                    "profile_name": profile_name or "default",
                    "created_at": started_at,
                    "started_at": started_at,
                    "finished_at": time.time(),
                    "error": str(exc),
                }
            )
        finally:
            KNOWLEDGE_INDEX_ACTIVE_JOBS.dec()

    def _run_collection_job(
        self,
        *,
        job_id: str,
        collection_id: str,
        artifact_ids: list[str],
        created_by: str | None,
        profile_name: str | None,
        profile_overrides: dict[str, Any] | None,
    ) -> None:
        KNOWLEDGE_INDEX_ACTIVE_JOBS.inc()
        started_at = time.time()
        self._save_job(
            {
                "job_id": job_id,
                "job_type": "collection",
                "scope_id": collection_id,
                "status": "running",
                "created_by": created_by,
                "profile_name": profile_name or "default",
                "created_at": started_at,
                "started_at": started_at,
                "artifact_ids": artifact_ids,
                "results": [],
            }
        )
        results: list[dict[str, Any]] = []
        status = "completed"
        try:
            for artifact_id in artifact_ids:
                knowledge_index, run = self._index_service.index_artifact(
                    artifact_id,
                    created_by=created_by,
                    profile_name=profile_name,
                    profile_overrides=profile_overrides,
                )
                item = {
                    "artifact_id": artifact_id,
                    "knowledge_index": knowledge_index.model_dump(),
                    "run": run.model_dump(),
                }
                results.append(item)
                if str(getattr(run, "status", "")) != "completed":
                    status = "failed"
            self._save_job(
                {
                    "job_id": job_id,
                    "job_type": "collection",
                    "scope_id": collection_id,
                    "status": status,
                    "created_by": created_by,
                    "profile_name": profile_name or "default",
                    "created_at": started_at,
                    "started_at": started_at,
                    "finished_at": time.time(),
                    "artifact_ids": artifact_ids,
                    "results": results,
                }
            )
        except Exception as exc:
            self._save_job(
                {
                    "job_id": job_id,
                    "job_type": "collection",
                    "scope_id": collection_id,
                    "status": "failed",
                    "created_by": created_by,
                    "profile_name": profile_name or "default",
                    "created_at": started_at,
                    "started_at": started_at,
                    "finished_at": time.time(),
                    "artifact_ids": artifact_ids,
                    "results": results,
                    "error": str(exc),
                }
            )
        finally:
            KNOWLEDGE_INDEX_ACTIVE_JOBS.dec()

    def _run_source_records_job(
        self,
        *,
        job_id: str,
        source_scope: str,
        source_id: str,
        records: list[dict[str, Any]],
        created_by: str | None,
        profile_name: str | None,
        source_metadata: dict[str, Any] | None,
        codecompass_prerender: bool = False,
    ) -> None:
        KNOWLEDGE_INDEX_ACTIVE_JOBS.inc()
        started_at = time.time()
        self._save_job(
            {
                "job_id": job_id,
                "job_type": "source_records",
                "scope_id": source_id,
                "source_scope": source_scope,
                "status": "running",
                "created_by": created_by,
                "profile_name": profile_name or "default",
                "created_at": started_at,
                "started_at": started_at,
                "record_count": len(records),
            }
        )
        try:
            knowledge_index, run = self._index_service.index_source_records(
                source_scope=source_scope,
                source_id=source_id,
                records=records,
                created_by=created_by,
                profile_name=profile_name,
                source_metadata=source_metadata,
                codecompass_prerender=codecompass_prerender,
            )
            self._save_job(
                {
                    "job_id": job_id,
                    "job_type": "source_records",
                    "scope_id": source_id,
                    "source_scope": source_scope,
                    "status": "completed" if str(getattr(run, "status", "")) == "completed" else "failed",
                    "created_by": created_by,
                    "profile_name": profile_name or "default",
                    "created_at": started_at,
                    "started_at": started_at,
                    "finished_at": time.time(),
                    "record_count": len(records),
                    "knowledge_index": knowledge_index.model_dump(),
                    "run": run.model_dump(),
                }
            )
        except Exception as exc:
            self._save_job(
                {
                    "job_id": job_id,
                    "job_type": "source_records",
                    "scope_id": source_id,
                    "source_scope": source_scope,
                    "status": "failed",
                    "created_by": created_by,
                    "profile_name": profile_name or "default",
                    "created_at": started_at,
                    "started_at": started_at,
                    "finished_at": time.time(),
                    "record_count": len(records),
                    "error": str(exc),
                }
            )
        finally:
            KNOWLEDGE_INDEX_ACTIVE_JOBS.dec()

    def submit_artifact_job(
        self,
        *,
        artifact_id: str,
        created_by: str | None,
        profile_name: str | None,
        profile_overrides: dict[str, Any] | None,
    ) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        self._save_job(
            {
                "job_id": job_id,
                "job_type": "artifact",
                "scope_id": artifact_id,
                "status": "queued",
                "created_by": created_by,
                "profile_name": profile_name or "default",
                "created_at": time.time(),
            }
        )
        self._executor.submit(
            self._run_artifact_job,
            job_id=job_id,
            artifact_id=artifact_id,
            created_by=created_by,
            profile_name=profile_name,
            profile_overrides=profile_overrides,
        )
        return self.get_job(job_id) or {}

    def submit_collection_job(
        self,
        *,
        collection_id: str,
        artifact_ids: list[str],
        created_by: str | None,
        profile_name: str | None,
        profile_overrides: dict[str, Any] | None,
    ) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        self._save_job(
            {
                "job_id": job_id,
                "job_type": "collection",
                "scope_id": collection_id,
                "status": "queued",
                "created_by": created_by,
                "profile_name": profile_name or "default",
                "created_at": time.time(),
                "artifact_ids": artifact_ids,
                "results": [],
            }
        )
        self._executor.submit(
            self._run_collection_job,
            job_id=job_id,
            collection_id=collection_id,
            artifact_ids=artifact_ids,
            created_by=created_by,
            profile_name=profile_name,
            profile_overrides=profile_overrides,
        )
        return self.get_job(job_id) or {}

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
        job_id = str(uuid.uuid4())
        self._save_job(
            {
                "job_id": job_id,
                "job_type": "source_records",
                "scope_id": source_id,
                "source_scope": source_scope,
                "status": "queued",
                "created_by": created_by,
                "profile_name": profile_name or "default",
                "created_at": time.time(),
                "record_count": len(records),
            }
        )
        self._executor.submit(
            self._run_source_records_job,
            job_id=job_id,
            source_scope=source_scope,
            source_id=source_id,
            records=records,
            created_by=created_by,
            profile_name=profile_name,
            source_metadata=source_metadata,
            codecompass_prerender=codecompass_prerender,
        )
        return self.get_job(job_id) or {}


knowledge_index_job_service = KnowledgeIndexJobService()


def get_knowledge_index_job_service() -> KnowledgeIndexJobService:
    return knowledge_index_job_service
