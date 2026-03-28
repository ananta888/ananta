from __future__ import annotations

from agent.db_models import MemoryEntryDB
from agent.repository import memory_entry_repo


class ResultMemoryService:
    """Persists worker results as hub-owned memory entries for later retrieval."""

    def record_worker_result_memory(
        self,
        *,
        task_id: str | None,
        goal_id: str | None,
        trace_id: str | None,
        worker_job_id: str | None,
        title: str | None,
        output: str | None,
        artifact_refs: list[dict] | None = None,
        retrieval_tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> MemoryEntryDB:
        raw_output = str(output or "")
        summary = raw_output[:280] if raw_output else None
        return memory_entry_repo.save(
            MemoryEntryDB(
                task_id=task_id,
                goal_id=goal_id,
                trace_id=trace_id,
                worker_job_id=worker_job_id,
                entry_type="worker_result",
                title=title,
                summary=summary,
                content=raw_output or None,
                artifact_refs=list(artifact_refs or []),
                retrieval_tags=list(retrieval_tags or []),
                memory_metadata=dict(metadata or {}),
            )
        )


result_memory_service = ResultMemoryService()


def get_result_memory_service() -> ResultMemoryService:
    return result_memory_service
