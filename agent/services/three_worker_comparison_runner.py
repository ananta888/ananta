from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from agent.services.three_worker_run_config_service import (
    DEFAULT_THREE_WORKER_RUN_CONFIG_PATH,
    ThreeWorkerRunConfigService,
)

TrackExecutor = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ThreeWorkerComparisonResult:
    status: str
    run_id: str
    planning: dict[str, Any]
    tracks: list[dict[str, Any]]
    summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "run_id": self.run_id,
            "planning": self.planning,
            "tracks": self.tracks,
            "summary": self.summary,
        }


class ThreeWorkerComparisonRunner:
    """Build and execute the three-worker comparison run.

    This runner is deliberately small: it resolves and validates the run config,
    enforces local-only planning, then calls a supplied track executor for each
    track. Production wiring can pass an executor that calls the existing propose
    endpoint/service. Tests can pass a deterministic fake executor.
    """

    def __init__(self, *, config_service: ThreeWorkerRunConfigService | None = None) -> None:
        self.config_service = config_service or ThreeWorkerRunConfigService()

    def build_plan(self, *, config_path: str | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
        cfg = self.config_service.resolve(config_path or DEFAULT_THREE_WORKER_RUN_CONFIG_PATH, env=env)
        provider_entries = self.config_service.build_provider_entries(cfg)
        return {
            "run_id": str((cfg.get("run") or {}).get("id") or "three-worker-local-planning"),
            "config": cfg,
            "planning": dict((cfg.get("planning") or {}).get("resolved") or {}),
            "provider_entries": provider_entries,
            "tracks": [track for track in list(cfg.get("tracks") or []) if isinstance(track, dict) and bool(track.get("enabled", True))],
        }

    def run(
        self,
        *,
        prompt: str,
        config_path: str | None = None,
        env: dict[str, str] | None = None,
        track_executor: TrackExecutor | None = None,
    ) -> ThreeWorkerComparisonResult:
        plan = self.build_plan(config_path=config_path, env=env)
        executor = track_executor or self._dry_run_executor
        results: list[dict[str, Any]] = []
        started = time.time()
        for index, track in enumerate(plan["tracks"]):
            track_started = time.time()
            try:
                result = executor(track, {"prompt": prompt, "planning": plan["planning"], "run_id": plan["run_id"], "track_index": index})
                status = str(result.get("status") or "ok").strip().lower() or "ok"
                results.append({
                    "track_id": track.get("id"),
                    "worker_type": track.get("worker_type"),
                    "requested_backend": track.get("requested_backend"),
                    "status": status,
                    "duration_ms": int((time.time() - track_started) * 1000),
                    "result": result,
                })
            except Exception as exc:
                results.append({
                    "track_id": track.get("id"),
                    "worker_type": track.get("worker_type"),
                    "requested_backend": track.get("requested_backend"),
                    "status": "failed",
                    "duration_ms": int((time.time() - track_started) * 1000),
                    "error": str(exc),
                })
        failed = [item for item in results if item.get("status") not in {"ok", "success", "dry_run"}]
        summary = {
            "track_count": len(results),
            "failed_count": len(failed),
            "provider_entries": list(plan["provider_entries"]),
            "duration_ms": int((time.time() - started) * 1000),
            "local_planning_enforced": True,
        }
        return ThreeWorkerComparisonResult(
            status="failed" if failed else "ok",
            run_id=plan["run_id"],
            planning=plan["planning"],
            tracks=results,
            summary=summary,
        )

    @staticmethod
    def _dry_run_executor(track: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "dry_run",
            "track_id": track.get("id"),
            "requested_backend": track.get("requested_backend"),
            "planning_provider": (context.get("planning") or {}).get("provider"),
            "planning_model": (context.get("planning") or {}).get("model"),
            "would_use_config_ref": track.get("config_ref"),
        }


def get_three_worker_comparison_runner() -> ThreeWorkerComparisonRunner:
    return ThreeWorkerComparisonRunner()
