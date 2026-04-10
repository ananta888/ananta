from __future__ import annotations

import re

from agent.db_models import MemoryEntryDB
from agent.repository import memory_entry_repo


class ResultMemoryService:
    """Persists worker results as hub-owned memory entries for later retrieval."""

    def _compact_output(self, output: str) -> dict[str, object]:
        text = str(output or "").strip()
        if not text:
            return {"summary": None, "compacted_summary": None, "bullet_points": []}
        normalized = re.sub(r"\s+", " ", text).strip()
        lines = [line.strip(" -\t") for line in text.splitlines() if line.strip()]
        bullets = []
        for line in lines:
            lowered = line.lower()
            if any(marker in lowered for marker in ("fix", "added", "changed", "updated", "removed", "test", "verify", "result")):
                bullets.append(line[:180])
            if len(bullets) >= 5:
                break
        if not bullets:
            bullets = [segment.strip()[:180] for segment in re.split(r"[.;]", normalized) if segment.strip()][:4]
        summary = normalized[:280]
        compacted = " | ".join(bullets)[:900]
        return {
            "summary": summary or None,
            "compacted_summary": compacted or None,
            "bullet_points": bullets,
        }

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
        compact = self._compact_output(raw_output)
        summary = str(compact.get("summary") or "") or (raw_output[:280] if raw_output else None)
        base_metadata = dict(metadata or {})
        return memory_entry_repo.save(
            MemoryEntryDB(
                task_id=task_id,
                goal_id=goal_id,
                trace_id=trace_id,
                worker_job_id=worker_job_id,
                entry_type="worker_result",
                title=title,
                summary=summary,
                content=(str(compact.get("compacted_summary") or "").strip() or raw_output or None),
                artifact_refs=list(artifact_refs or []),
                retrieval_tags=list(retrieval_tags or []),
                memory_metadata={
                    **base_metadata,
                    "compacted_summary": compact.get("compacted_summary"),
                    "bullet_points": list(compact.get("bullet_points") or []),
                    "original_output_chars": len(raw_output),
                },
            )
        )


result_memory_service = ResultMemoryService()


def get_result_memory_service() -> ResultMemoryService:
    return result_memory_service
