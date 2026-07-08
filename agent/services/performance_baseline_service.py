"""Baseline lookup and persistence for performance experiments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.performance.artifacts import stable_hash, utc_now


class PerformanceBaselineService:
    def __init__(self, root: str | Path = "data/performance-baselines") -> None:
        self._root = Path(root)

    def save_baseline(
        self,
        *,
        benchmark_run: dict[str, Any],
        repo_ref: str,
        profile_id: str,
        hardware_fingerprint: dict[str, Any] | None = None,
        software_fingerprint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "schema": "performance_baseline_record.v1",
            "baseline_id": f"base-{stable_hash([repo_ref, profile_id, benchmark_run.get('run_id')])[:16]}",
            "repo_ref": repo_ref,
            "profile_id": profile_id,
            "benchmark_run": benchmark_run,
            "hardware_fingerprint": hardware_fingerprint or benchmark_run.get("hardware_fingerprint") or {},
            "software_fingerprint": software_fingerprint or benchmark_run.get("software_fingerprint") or {},
            "created_at": utc_now(),
            "status": "active",
        }
        path = self._path_for(record["baseline_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        return record

    def find_baseline(
        self,
        *,
        repo_ref: str,
        profile_id: str,
        hardware_fingerprint: dict[str, Any] | None = None,
        software_fingerprint: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        candidates = []
        for path in sorted(self._root.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if record.get("repo_ref") == repo_ref and record.get("profile_id") == profile_id:
                score = 0
                if hardware_fingerprint and record.get("hardware_fingerprint") == hardware_fingerprint:
                    score += 2
                if software_fingerprint and record.get("software_fingerprint") == software_fingerprint:
                    score += 2
                candidates.append((score, record))
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]

    def _path_for(self, baseline_id: str) -> Path:
        return self._root / f"{baseline_id}.json"


def get_performance_baseline_service() -> PerformanceBaselineService:
    return PerformanceBaselineService()
