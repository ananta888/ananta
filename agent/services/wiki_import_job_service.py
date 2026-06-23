from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from agent.config import settings
from agent.services.ingestion_service import get_ingestion_service
from agent.services.rag_helper_index_service import get_rag_helper_index_service
from agent.services.wiki_record_writer import compact_wiki_jsonl

logger = logging.getLogger(__name__)

_JOBS_FILE = Path(settings.data_dir) / "wiki_import_jobs.json"


class WikiImportJobService:
    """Dedicated orchestration for wiki import phases with lightweight controls."""

    def __init__(self, ingestion_service=None, index_service=None, *, max_workers: int = 1) -> None:
        self._ingestion = ingestion_service or get_ingestion_service()
        self._index = index_service or get_rag_helper_index_service()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="wiki-import")
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._load_jobs()

    def _load_jobs(self) -> None:
        try:
            if _JOBS_FILE.exists():
                data = json.loads(_JOBS_FILE.read_text(encoding="utf-8"))
                jobs = data if isinstance(data, dict) else {}
                interrupted: list[str] = []
                for job_id, job in jobs.items():
                    if job.get("status") in {"running", "queued"}:
                        job = {**job, "status": "interrupted", "phase": "interrupted",
                               "error": "Hub wurde neu gestartet — Import unterbrochen", "finished_at": time.time()}
                        interrupted.append(job_id[:8])
                    self._jobs[job_id] = job
                if interrupted:
                    logger.warning("wiki_import_job_service: %d interrupted jobs on startup: %s", len(interrupted), interrupted)
                logger.info("wiki_import_job_service: loaded %d jobs from disk", len(self._jobs))
        except Exception:
            logger.exception("wiki_import_job_service: failed to load jobs from disk")

    def _flush(self) -> None:
        try:
            _JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _JOBS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._jobs, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(_JOBS_FILE)
        except Exception:
            logger.exception("wiki_import_job_service: failed to flush jobs to disk")

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._jobs.get(job_id)
            return dict(item) if item else None

    def _save(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._jobs[str(payload["job_id"])] = payload
            self._flush()
        return payload

    def retry_interrupted_job(self, job_id: str) -> dict[str, Any] | None:
        """Re-submits an interrupted or failed job. Resumes from checkpoint if available."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") not in {"interrupted", "failed"}:
                return None
            job = {**job, "status": "queued", "phase": "queued", "progress_percent": 0,
                   "error": None, "finished_at": None, "started_at": None}
            self._jobs[job_id] = job
            self._flush()
        self._executor.submit(self._run_job, job_id=job_id)
        return self.get_job(job_id)

    def pause_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.get("status") in {"queued", "running"}:
                job = dict(job)
                job["status"] = "paused"
                job["pause_requested"] = True
                self._jobs[job_id] = job
                self._flush()
            return dict(self._jobs[job_id])

    def resume_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.get("status") == "paused":
                job = dict(job)
                job["status"] = "running"
                job["pause_requested"] = False
                self._jobs[job_id] = job
            return dict(self._jobs[job_id])

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            job = dict(job)
            job["cancel_requested"] = True
            if job.get("status") in {"queued", "paused"}:
                job["status"] = "cancelled"
                job["phase"] = "cancelled"
                job["finished_at"] = time.time()
            self._jobs[job_id] = job
            return dict(job)

    def submit_import_job(
        self,
        *,
        import_request: dict[str, Any],
        from_url: bool,
        created_by: str | None,
    ) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        base = {
            "job_id": job_id,
            "job_type": "wiki_import",
            "status": "queued",
            "phase": "queued",
            "progress_percent": 0,
            "created_by": created_by,
            "created_at": time.time(),
            "request": dict(import_request),
            "from_url": bool(from_url),
            "pause_requested": False,
            "cancel_requested": False,
        }
        self._save(base)
        self._executor.submit(self._run_job, job_id=job_id)
        return self.get_job(job_id) or {}

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: float(j.get("created_at") or 0), reverse=True)

    def _run_job(self, *, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        request = dict(job.get("request") or {})
        self._save({**job, "status": "running", "phase": "download_parse_normalize", "progress_percent": 15, "started_at": time.time()})

        def _is_cancelled() -> bool:
            current = self.get_job(job_id)
            return bool(current and current.get("cancel_requested"))

        import math

        def _on_parse_progress(items_done: int, records_done: int) -> None:
            _ESTIMATE = 2_700_000
            ratio = min(1.0, items_done / _ESTIMATE)
            pct = 15 + int(math.log1p(ratio * (math.e - 1)) * 55)
            current = self.get_job(job_id) or {}
            self._save({**current, "progress_percent": pct,
                        "parse_items_done": items_done, "parse_records_done": records_done})

        _max_chunks = int(request.get("max_chunks_per_article") or 3)
        _min_chars  = int(request.get("min_content_chars") or 300)

        try:
            if _is_cancelled():
                self._save({**self.get_job(job_id), "status": "cancelled", "phase": "cancelled", "progress_percent": 100, "finished_at": time.time()})
                return
            if bool(job.get("from_url")):
                report = self._ingestion.import_wiki_jsonl_from_url(
                    corpus_url=request["corpus_url"],
                    index_url=request.get("index_url"),
                    source_id=request.get("source_id"),
                    default_language=request.get("language", "en"),
                    strict=bool(request.get("strict", False)),
                    cancel_check=_is_cancelled,
                    progress_callback=_on_parse_progress,
                    max_chunks_per_article=_max_chunks,
                    min_content_chars=_min_chars,
                )
            else:
                report = self._ingestion.import_wiki_corpus(
                    corpus_path=request["corpus_path"],
                    index_path=request.get("index_path"),
                    source_id=request.get("source_id"),
                    default_language=request.get("language", "en"),
                    strict=bool(request.get("strict", False)),
                    import_format=request.get("import_format"),
                    cancel_check=_is_cancelled,
                    progress_callback=_on_parse_progress,
                    max_chunks_per_article=_max_chunks,
                    min_content_chars=_min_chars,
                )
            current = self.get_job(job_id) or {}
            if bool(current.get("cancel_requested")):
                self._save({**current, "status": "cancelled", "phase": "cancelled", "progress_percent": 100,
                            "import_report": {k: v for k, v in report.items() if k != "records"}, "finished_at": time.time()})
                return
            if bool(current.get("pause_requested")):
                self._save({**current, "status": "paused", "phase": "paused_after_import", "progress_percent": 74,
                            "import_report": {k: v for k, v in report.items() if k != "records"}})
                return

            # Pass file path directly — no in-memory load (records list is empty when write_jsonl_cache=True)
            jsonl_cache  = report.get("jsonl_cache_path") or ""
            links_cache  = report.get("links_cache_path") or ""
            in_memory    = list(report.get("records") or [])

            current = self.get_job(job_id) or {}
            self._save({**current, "status": "running", "phase": "index", "progress_percent": 75,
                        "import_report": {k: v for k, v in report.items() if k != "records"}})
            source_metadata = {
                **dict(request.get("source_metadata") or {}),
                "issues": list(report.get("issues") or []),
                "import_stats": dict(report.get("stats") or {}),
                "links_cache": links_cache,
            }
            index_obj, run = self._index.index_source_records(
                source_scope="wiki",
                source_id=str(report.get("source_id") or ""),
                records=in_memory,
                records_path=Path(jsonl_cache) if jsonl_cache and not in_memory else None,
                created_by=current.get("created_by"),
                profile_name=request.get("profile_name"),
                source_metadata=source_metadata,
                codecompass_prerender=bool(request.get("codecompass_prerender", False)),
                links_path=Path(links_cache) if links_cache else None,
            )
            self._save(
                {
                    **(self.get_job(job_id) or {}),
                    "status": "completed" if str(getattr(run, "status", "")) == "completed" else "failed",
                    "phase": "completed" if str(getattr(run, "status", "")) == "completed" else "failed",
                    "progress_percent": 100,
                    "knowledge_index": index_obj.model_dump(),
                    "run": run.model_dump(),
                    "finished_at": time.time(),
                }
            )
        except Exception as exc:
            cancelled = "wiki_download_cancelled" in str(exc)
            self._save(
                {
                    **(self.get_job(job_id) or {"job_id": job_id}),
                    "status": "cancelled" if cancelled else "failed",
                    "phase": "cancelled" if cancelled else "failed",
                    "progress_percent": 100,
                    "error": None if cancelled else str(exc),
                    "finished_at": time.time(),
                }
            )


wiki_import_job_service = WikiImportJobService()


def get_wiki_import_job_service() -> WikiImportJobService:
    return wiki_import_job_service
