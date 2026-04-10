from __future__ import annotations

import re

from agent.db_models import MemoryEntryDB
from agent.repository import memory_entry_repo


class ResultMemoryService:
    """Persists worker results as hub-owned memory entries for later retrieval."""

    _FILE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|java|go|rs|md|json|yaml|yml|xml))(?![A-Za-z0-9_./-])")

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

    def _extract_changed_files(self, text: str) -> list[str]:
        files: list[str] = []
        for match in self._FILE_PATH_RE.findall(text or ""):
            path = str(match[0] if isinstance(match, tuple) else match).strip()
            if path and path not in files:
                files.append(path)
            if len(files) >= 8:
                break
        return files

    def _structured_summary(self, *, output: str, compacted_summary: str, bullets: list[str]) -> dict[str, object]:
        normalized = re.sub(r"\s+", " ", str(output or "")).strip()
        lowered = normalized.lower()
        status = "completed"
        if any(token in lowered for token in ("failed", "exception", "traceback", "error", "regression")):
            status = "attention_needed"

        changed_files = self._extract_changed_files(output)
        test_pass = any(token in lowered for token in ("test passed", "tests passed", "all tests passed", "passed"))
        test_fail = any(token in lowered for token in ("test failed", "tests failed", "failing test", "failed"))
        next_steps: list[str] = []
        risks: list[str] = []
        for line in [line.strip() for line in str(output or "").splitlines() if line.strip()]:
            line_l = line.lower()
            if any(marker in line_l for marker in ("next step", "todo", "follow-up", "followup")):
                next_steps.append(line[:200])
            if any(marker in line_l for marker in ("risk", "warning", "blocked", "pending", "todo", "fixme")):
                risks.append(line[:200])
            if len(next_steps) >= 4 and len(risks) >= 4:
                break

        focus_terms: list[str] = []
        for value in [*changed_files, *(bullets or [])]:
            for token in re.findall(r"[A-Za-z0-9_]+", str(value or "").lower()):
                if len(token) < 4:
                    continue
                if token not in focus_terms:
                    focus_terms.append(token)
                if len(focus_terms) >= 16:
                    break
            if len(focus_terms) >= 16:
                break

        return {
            "status": status,
            "changed_files": changed_files,
            "tests": {
                "passed_signal": test_pass,
                "failed_signal": test_fail,
            },
            "next_steps": next_steps[:4],
            "risks": risks[:4],
            "focus_terms": focus_terms,
            "compacted_summary": str(compacted_summary or "").strip() or None,
        }

    def _build_retrieval_document(
        self,
        *,
        summary: str | None,
        bullets: list[str],
        structured: dict[str, object],
    ) -> str:
        lines: list[str] = []
        if summary:
            lines.append(f"summary: {summary}")
        status = str(structured.get("status") or "").strip()
        if status:
            lines.append(f"status: {status}")
        changed_files = [str(item).strip() for item in list(structured.get("changed_files") or []) if str(item).strip()]
        if changed_files:
            lines.append(f"changed_files: {', '.join(changed_files)}")
        tests = dict(structured.get("tests") or {})
        lines.append(
            f"tests: passed_signal={bool(tests.get('passed_signal'))}; failed_signal={bool(tests.get('failed_signal'))}"
        )
        risks = [str(item).strip() for item in list(structured.get("risks") or []) if str(item).strip()]
        if risks:
            lines.append("risks: " + " | ".join(risks[:4]))
        next_steps = [str(item).strip() for item in list(structured.get("next_steps") or []) if str(item).strip()]
        if next_steps:
            lines.append("next_steps: " + " | ".join(next_steps[:4]))
        if bullets:
            lines.append("highlights: " + " | ".join(str(item) for item in bullets[:6]))
        return "\n".join(line for line in lines if line).strip()[:2200]

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
        structured = self._structured_summary(
            output=raw_output,
            compacted_summary=str(compact.get("compacted_summary") or ""),
            bullets=list(compact.get("bullet_points") or []),
        )
        retrieval_document = self._build_retrieval_document(
            summary=summary,
            bullets=list(compact.get("bullet_points") or []),
            structured=structured,
        )
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
                content=(retrieval_document or str(compact.get("compacted_summary") or "").strip() or raw_output or None),
                artifact_refs=list(artifact_refs or []),
                retrieval_tags=list(retrieval_tags or []),
                memory_metadata={
                    **base_metadata,
                    "compacted_summary": compact.get("compacted_summary"),
                    "bullet_points": list(compact.get("bullet_points") or []),
                    "structured_summary": structured,
                    "retrieval_document": retrieval_document or None,
                    "memory_format": "worker_result_compact_v2",
                    "original_output_chars": len(raw_output),
                },
            )
        )


result_memory_service = ResultMemoryService()


def get_result_memory_service() -> ResultMemoryService:
    return result_memory_service
