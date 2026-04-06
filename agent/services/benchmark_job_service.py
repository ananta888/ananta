from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from agent.repository import worker_job_repo, worker_result_repo
from agent.services.hub_benchmark_service import get_hub_benchmark_service
from agent.services.ollama_benchmark_service import get_ollama_benchmark_service
from agent.services.worker_job_service import get_worker_job_service

logger = logging.getLogger(__name__)


class BenchmarkJobService:
    """Shared async execution core for long-running benchmark runs."""

    def __init__(self, *, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="benchmark-job")
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._worker_job_service = get_worker_job_service()

    def _save_job(self, job: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._jobs[str(job["job_id"])] = dict(job)
            return dict(self._jobs[str(job["job_id"])])

    def _update_runtime_job(self, job_id: str, **updates: Any) -> dict[str, Any] | None:
        with self._lock:
            current = dict(self._jobs.get(job_id) or {})
            if not current:
                return None
            current.update({key: value for key, value in updates.items() if value is not None})
            self._jobs[job_id] = current
            return dict(current)

    def _persist_job_status(self, job_id: str, *, status: str, metadata: dict[str, Any] | None = None) -> None:
        job = worker_job_repo.get_by_id(job_id)
        if job is None:
            return
        job.status = status
        job.updated_at = time.time()
        if metadata:
            merged = dict(job.job_metadata or {})
            merged.update(metadata)
            job.job_metadata = merged
        worker_job_repo.save(job)

    def _persist_job_result(self, job_id: str, *, job_type: str, status: str, result: dict[str, Any] | None) -> None:
        payload = result or {}
        self._worker_job_service.record_worker_result(
            worker_job_id=job_id,
            task_id=f"benchmark:{job_type}",
            worker_url=f"hub://benchmark/{job_type}",
            status=status,
            output=json.dumps(payload, ensure_ascii=False),
            metadata={"benchmark_job_type": job_type, "summary": self._build_summary(payload)},
        )

    @staticmethod
    def _build_summary(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": result.get("status"),
            "total_tests": result.get("total_tests") or (result.get("summary") or {}).get("total_tests"),
            "successful": result.get("successful") or (result.get("summary") or {}).get("successful"),
            "failed": result.get("failed") or (result.get("summary") or {}).get("failed"),
            "duration_seconds": result.get("duration_seconds"),
        }

    def _submit_job(
        self,
        *,
        job_type: str,
        created_by: str | None,
        request_payload: dict[str, Any],
        runner: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        worker_job = self._worker_job_service.create_worker_job(
            parent_task_id=f"benchmark:{job_type}",
            subtask_id=f"{job_type}-run",
            worker_url=f"hub://benchmark/{job_type}",
            context_bundle_id=None,
            allowed_tools=[],
            expected_output_schema={},
            metadata={
                "benchmark_job_type": job_type,
                "created_by": created_by or "anonymous",
                "request": dict(request_payload),
                "execution_mode": "async",
            },
        )
        self._persist_job_status(
            worker_job.id,
            status="queued",
            metadata={"queued_at": time.time()},
        )
        job = self._save_job(
            {
                "job_id": worker_job.id,
                "worker_job_id": worker_job.id,
                "job_type": job_type,
                "status": "queued",
                "created_by": created_by or "anonymous",
                "created_at": time.time(),
                "request": dict(request_payload),
            }
        )
        self._executor.submit(self._run_job, job_id=worker_job.id, job_type=job_type, runner=runner)
        return job

    def _run_job(self, *, job_id: str, job_type: str, runner: Callable[[], dict[str, Any]]) -> None:
        started_at = time.time()
        self._persist_job_status(job_id, status="running", metadata={"started_at": started_at})
        self._update_runtime_job(job_id, status="running", started_at=started_at)
        try:
            result = runner() or {}
            final_status = "completed" if str(result.get("status") or "completed") == "completed" else "failed"
            finished_at = time.time()
            self._persist_job_status(job_id, status=final_status, metadata={"finished_at": finished_at})
            self._persist_job_result(job_id, job_type=job_type, status=final_status, result=result)
            self._update_runtime_job(
                job_id,
                status=final_status,
                finished_at=finished_at,
                result=result,
                summary=self._build_summary(result),
            )
        except Exception as exc:
            finished_at = time.time()
            logger.warning("Benchmark job %s failed: %s", job_id, exc)
            failure = {"status": "failed", "error": str(exc)}
            self._persist_job_status(job_id, status="failed", metadata={"finished_at": finished_at, "error": str(exc)})
            self._persist_job_result(job_id, job_type=job_type, status="failed", result=failure)
            self._update_runtime_job(
                job_id,
                status="failed",
                finished_at=finished_at,
                error=str(exc),
                result=failure,
                summary=self._build_summary(failure),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            runtime_job = self._jobs.get(job_id)
            if runtime_job is not None:
                return dict(runtime_job)

        job = worker_job_repo.get_by_id(job_id)
        if job is None:
            return None
        results = worker_result_repo.get_by_worker_job(job_id)
        latest_result = results[0] if results else None
        payload: dict[str, Any] | None = None
        if latest_result and latest_result.output:
            try:
                parsed = json.loads(latest_result.output)
                payload = parsed if isinstance(parsed, dict) else {"raw_output": latest_result.output}
            except json.JSONDecodeError:
                payload = {"raw_output": latest_result.output}
        return {
            "job_id": job.id,
            "worker_job_id": job.id,
            "job_type": str((job.job_metadata or {}).get("benchmark_job_type") or ""),
            "status": job.status,
            "created_by": str((job.job_metadata or {}).get("created_by") or "anonymous"),
            "created_at": job.created_at,
            "request": dict((job.job_metadata or {}).get("request") or {}),
            "result": payload,
            "summary": dict((latest_result.result_metadata or {}).get("summary") or {}) if latest_result else {},
        }

    def submit_hub_benchmark_job(
        self,
        *,
        roles: list[str] | None,
        providers: list[str] | None,
        max_execution_minutes: int,
        created_by: str | None,
    ) -> dict[str, Any]:
        return self._submit_job(
            job_type="hub_benchmark",
            created_by=created_by,
            request_payload={
                "roles": list(roles or []),
                "providers": list(providers or []),
                "max_execution_minutes": max_execution_minutes,
            },
            runner=lambda: get_hub_benchmark_service().run_full_benchmark(
                roles=roles,
                providers=providers,
                max_execution_minutes=max_execution_minutes,
            ),
        )

    def submit_ollama_benchmark_job(
        self,
        *,
        models: list[str] | None,
        roles: list[str] | None,
        parameter_variations: bool,
        max_execution_minutes: int,
        base_url: str | None,
        created_by: str | None,
    ) -> dict[str, Any]:
        return self._submit_job(
            job_type="ollama_benchmark",
            created_by=created_by,
            request_payload={
                "models": list(models or []),
                "roles": list(roles or []),
                "parameter_variations": parameter_variations,
                "max_execution_minutes": max_execution_minutes,
                "base_url": base_url,
            },
            runner=lambda: get_ollama_benchmark_service().run_full_benchmark(
                models=models,
                roles=roles,
                parameter_variations=parameter_variations,
                max_execution_minutes=max_execution_minutes,
                base_url=base_url,
            ),
        )


benchmark_job_service = BenchmarkJobService()


def get_benchmark_job_service() -> BenchmarkJobService:
    return benchmark_job_service
