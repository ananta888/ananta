"""Policy-bound benchmark runner for performance experiments."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

from agent.performance.artifacts import build_benchmark_run_artifact, safe_ref_text, utc_now
from agent.services.native_worker_runtime_service import (
    NativeWorkerRuntimeService,
    get_native_worker_runtime_service,
)

_SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


class BenchmarkRunnerService:
    """Run benchmark commands through the native worker runtime."""

    def __init__(self, runtime: NativeWorkerRuntimeService | None = None) -> None:
        self._runtime = runtime or get_native_worker_runtime_service()

    def run_benchmark(
        self,
        *,
        command: str,
        workspace_dir: str | Path,
        task_id: str = "performance-benchmark",
        profile_id: str = "micro_benchmark",
        timeout_seconds: int = 30,
        warmup_runs: int = 0,
        measured_runs: int = 1,
        agent_cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workspace = Path(workspace_dir).resolve()
        run_group_id = f"bench-group-{uuid.uuid4().hex[:12]}"
        runs: list[dict[str, Any]] = []
        total = max(1, int(warmup_runs) + int(measured_runs))
        for index in range(total):
            is_warmup = index < int(warmup_runs)
            run = self._run_once(
                command=command,
                workspace_dir=workspace,
                task_id=task_id,
                profile_id=profile_id,
                timeout_seconds=timeout_seconds,
                agent_cfg=agent_cfg,
            )
            run["run_group_id"] = run_group_id
            run["warmup"] = is_warmup
            runs.append(run)
        measured = [run for run in runs if not run.get("warmup")]
        if len(measured) == 1:
            return measured[0]
        return {
            "schema": "benchmark_run_group.v1",
            "run_group_id": run_group_id,
            "profile_id": profile_id,
            "task_id": task_id,
            "runs": runs,
            "measured_runs": measured,
            "status": "completed" if measured and all(r.get("status") == "completed" for r in measured) else "degraded",
        }

    def _run_once(
        self,
        *,
        command: str,
        workspace_dir: Path,
        task_id: str,
        profile_id: str,
        timeout_seconds: int,
        agent_cfg: dict[str, Any] | None,
    ) -> dict[str, Any]:
        started_at = utc_now()
        started = time.monotonic()
        cfg = self._native_cfg(agent_cfg)
        trace_id = f"benchmark-{uuid.uuid4().hex[:12]}"
        try:
            prepared = self._runtime.prepare_native_command_plan(
                tid=task_id,
                task={"id": task_id, "goal_id": "performance"},
                command=command,
                reason="performance benchmark",
                worker_profile="balanced",
                profile_source="performance_runner",
                trace_id=trace_id,
                context_bundle_id=f"ctx-{task_id}",
                agent_cfg=cfg,
            )
            native_payload = dict((prepared.get("worker_context_updates") or {}).get("native_runtime") or {})
            result = self._runtime.execute_and_verify_command(
                tid=task_id,
                task={"id": task_id, "goal_id": "performance"},
                command=command,
                trace_id=trace_id,
                worker_profile="balanced",
                profile_source="performance_runner",
                timeout_seconds=int(timeout_seconds),
                workspace_dir=workspace_dir,
                native_runtime_payload=native_payload,
                agent_cfg=cfg,
            )
        except Exception as exc:
            return build_benchmark_run_artifact(
                run_id=f"bench-{uuid.uuid4().hex[:12]}",
                task_id=task_id,
                profile_id=profile_id,
                command=command,
                cwd=str(workspace_dir),
                started_at=started_at,
                duration_seconds=time.monotonic() - started,
                exit_code=1,
                metrics={"wall_time": {"samples": [time.monotonic() - started]}},
                stderr_ref=str(exc),
                status="degraded",
                reason_code="runtime_exception",
                env_sanitized=self._sanitize_env(),
            )
        native = dict(result.get("native_runtime") or {})
        test_result = dict(native.get("test_result_artifact") or {})
        stdout, stdout_truncated = safe_ref_text(str(test_result.get("stdout_ref") or result.get("output") or ""))
        stderr, stderr_truncated = safe_ref_text(str(test_result.get("stderr_ref") or ""))
        raw_exit_code = result.get("exit_code") if result.get("exit_code") is not None else test_result.get("exit_code")
        exit_code = int(raw_exit_code or 1)
        warnings = []
        if stdout_truncated or stderr_truncated:
            warnings.append("output_truncated")
        status = "completed" if str(result.get("status") or "") == "completed" and exit_code == 0 else "failed"
        reason_code = "success" if status == "completed" else str(result.get("failure_type") or "command_failed")
        duration = time.monotonic() - started
        return build_benchmark_run_artifact(
            run_id=f"bench-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            profile_id=profile_id,
            command=command,
            cwd=str(workspace_dir),
            started_at=started_at,
            duration_seconds=duration,
            exit_code=exit_code,
            metrics={"wall_time": {"samples": [duration]}},
            stdout_ref=stdout,
            stderr_ref=stderr,
            artifacts=list(result.get("artifact_refs") or []),
            warnings=warnings,
            status=status,
            reason_code=reason_code,
            env_sanitized=self._sanitize_env(),
        )

    @staticmethod
    def _native_cfg(agent_cfg: dict[str, Any] | None) -> dict[str, Any]:
        if agent_cfg:
            return agent_cfg
        return {
            "worker_runtime": {
                "native_worker_runtime": {
                    "enabled": True,
                    "shell_policy": {
                        "allowlist": ["python", "pytest", "echo"],
                        "approval_required_commands": ["rm", "mv", "chmod", "chown", "sudo"],
                        "denylist_tokens": ["rm -rf /", "mkfs", ":(){"],
                    },
                }
            }
        }

    @staticmethod
    def _sanitize_env() -> dict[str, str]:
        safe: dict[str, str] = {}
        for key in ("PATH", "HOME", "PYTHONPATH"):
            value = os.environ.get(key)
            if value and not any(marker in key.upper() for marker in _SECRET_ENV_MARKERS):
                safe[key] = "<set>"
        return safe


def get_benchmark_runner_service() -> BenchmarkRunnerService:
    return BenchmarkRunnerService()
