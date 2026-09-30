"""Terminal bookkeeping of a rag-helper knowledge-index run.

Split out of ``rag_helper_index_service`` (SRP): marking a run and its
knowledge index completed or failed, persisting both (unless the caller owns
no control-plane records) and counting the outcome in the index metrics.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agent.db_models import KnowledgeIndexDB, KnowledgeIndexRunDB
from agent.metrics import KNOWLEDGE_INDEX_DURATION_SECONDS, KNOWLEDGE_INDEX_RUNS_TOTAL


class KnowledgeIndexRunRecorder:
    """Record the terminal state of one index run on the run and its index."""

    def __init__(self, *, knowledge_index_repository: Any, knowledge_index_run_repository: Any) -> None:
        self._knowledge_index_repo = knowledge_index_repository
        self._knowledge_index_run_repo = knowledge_index_run_repository

    def completed(
        self,
        knowledge_index: KnowledgeIndexDB,
        run: KnowledgeIndexRunDB,
        *,
        manifest: dict[str, Any],
        index_metadata: dict[str, Any],
        started: float,
        output_dir: Path,
        manifest_path: Path,
        source_scope: str,
        profile_name: str,
        persist: bool = True,
    ) -> tuple[KnowledgeIndexDB, KnowledgeIndexRunDB]:
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        run.status = "completed"
        run.output_dir = str(output_dir)
        run.manifest_path = str(manifest_path)
        run.duration_ms = duration_ms
        run.finished_at = time.time()
        run.run_metadata = {**(run.run_metadata or {}), "manifest": manifest}
        if persist:
            run = self._knowledge_index_run_repo.save(run)

        knowledge_index.status = "completed"
        knowledge_index.latest_run_id = run.id
        knowledge_index.output_dir = str(output_dir)
        knowledge_index.manifest_path = str(manifest_path)
        knowledge_index.updated_at = time.time()
        knowledge_index.index_metadata = {
            **(knowledge_index.index_metadata or {}),
            **index_metadata,
        }
        if persist:
            knowledge_index = self._knowledge_index_repo.save(knowledge_index)
        KNOWLEDGE_INDEX_RUNS_TOTAL.labels(scope=source_scope, status="completed", profile=profile_name).inc()
        KNOWLEDGE_INDEX_DURATION_SECONDS.labels(scope=source_scope, profile=profile_name).observe(
            duration_ms / 1000.0
        )
        return knowledge_index, run

    def failed(
        self,
        knowledge_index: KnowledgeIndexDB,
        run: KnowledgeIndexRunDB,
        *,
        exc: Exception,
        started: float,
        output_dir: Path,
        manifest_path: Path,
        source_scope: str,
        profile_name: str,
        persist: bool = True,
    ) -> tuple[KnowledgeIndexDB, KnowledgeIndexRunDB]:
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        run.status = "failed"
        run.output_dir = str(output_dir)
        run.manifest_path = str(manifest_path)
        run.duration_ms = duration_ms
        run.error_message = str(exc)
        run.finished_at = time.time()
        if persist:
            run = self._knowledge_index_run_repo.save(run)

        knowledge_index.status = "failed"
        knowledge_index.latest_run_id = run.id
        knowledge_index.output_dir = str(output_dir)
        knowledge_index.manifest_path = str(manifest_path)
        knowledge_index.updated_at = time.time()
        knowledge_index.index_metadata = {
            **(knowledge_index.index_metadata or {}),
            "last_error": str(exc),
        }
        if persist:
            knowledge_index = self._knowledge_index_repo.save(knowledge_index)
        KNOWLEDGE_INDEX_RUNS_TOTAL.labels(scope=source_scope, status="failed", profile=profile_name).inc()
        KNOWLEDGE_INDEX_DURATION_SECONDS.labels(scope=source_scope, profile=profile_name).observe(
            duration_ms / 1000.0
        )
        return knowledge_index, run


__all__ = ["KnowledgeIndexRunRecorder"]
