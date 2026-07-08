"""Build bounded context packages for optimization workers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class PerformanceContextOrchestrator:
    def build_context_package(
        self,
        *,
        hypothesis: dict[str, Any],
        workspace_dir: str | Path = ".",
        max_files: int = 5,
        max_total_bytes: int = 20000,
    ) -> dict[str, Any]:
        root = Path(workspace_dir).resolve()
        files = []
        used = 0
        for raw_path in list(hypothesis.get("affected_files") or [])[:max_files]:
            path = (root / str(raw_path)).resolve()
            if root != path and root not in path.parents:
                continue
            if not path.exists() or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            remaining = max_total_bytes - used
            if remaining <= 0:
                break
            excerpt = text[:remaining]
            used += len(excerpt.encode("utf-8"))
            files.append({
                "path": str(path.relative_to(root)),
                "content": excerpt,
                "why_this_context": f"affected by hypothesis {hypothesis.get('hypothesis_id')}",
                "authoritative": True,
            })
        return {
            "schema": "performance_context_package.v1",
            "hypothesis_id": hypothesis.get("hypothesis_id"),
            "files": files,
            "byte_count": used,
            "truncated": used >= max_total_bytes,
        }


def get_performance_context_orchestrator() -> PerformanceContextOrchestrator:
    return PerformanceContextOrchestrator()
