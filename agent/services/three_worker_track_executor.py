from __future__ import annotations

import time
from typing import Any, Callable

from worker.core.context_resolver import ContextBlock, ContextSensitivity
from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope, ModelPolicy
from worker.core.hermes_adapter import HermesAdapter
from worker.core.hermes_default_config import build_hermes_adapter_config_from_agent_config

CliRunner = Callable[..., tuple[int, str, str, str]]


class ThreeWorkerTrackExecutor:
    """Execute one track of the three-worker comparison run.

    Hermes is wired directly to the governed HermesAdapter.
    OpenCode and ananta-worker are wired through the same CLI runner contract used
    by task scoped execution. If no cli_runner is supplied, the executor returns a
    handoff_required result instead of pretending success.
    """

    def __init__(self, *, agent_cfg: dict[str, Any] | None = None, cli_runner: CliRunner | None = None) -> None:
        self.agent_cfg = dict(agent_cfg or {})
        self.cli_runner = cli_runner

    def __call__(self, track: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        backend = str(track.get("requested_backend") or track.get("worker_type") or "").strip().lower()
        if backend == "hermes":
            return self._execute_hermes(track, context)
        if backend in {"opencode", "ananta-worker"}:
            return self._execute_local_cli_backend(track, context, backend=backend)
        return {"status": "failed", "reason": f"unsupported_track_backend:{backend or '<empty>'}"}

    def _execute_hermes(self, track: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        cfg = build_hermes_adapter_config_from_agent_config({
            **self.agent_cfg,
            "worker": {"type": "hermes"},
        })
        adapter = HermesAdapter(config=cfg)
        prompt = str(context.get("prompt") or "").strip()
        envelope = ExecutionEnvelope(
            task_id=f"three-worker-{track.get('id') or 'hermes'}",
            actor_ref="ananta:three-worker-runner",
            capability_grant=CapabilityGrant(capabilities=["planning", "review", "summarize", "patch_propose", "research_limited"]),
            context_envelope_ref="three-worker:context",
            audit_correlation_id=f"three-worker:{context.get('run_id') or 'run'}:{track.get('id') or 'hermes'}",
            model_policy=ModelPolicy(
                cloud_allowed=bool((track.get("cloud_policy") or {}).get("cloud_allowed", True)),
                preferred_model=None,
            ),
        )
        result = adapter.plan_only(
            envelope,
            context_blocks=[
                ContextBlock(
                    source_type="three_worker_prompt",
                    origin_id="cli_prompt",
                    provenance="three_worker_track_executor",
                    sensitivity=ContextSensitivity.internal,
                    content=prompt,
                    priority=0,
                )
            ],
        )
        return {
            "status": result.status.value,
            "track_id": track.get("id"),
            "requested_backend": "hermes",
            "worker_type": "hermes",
            "summary": result.summary,
            "artifacts": [artifact.model_dump() for artifact in result.artifacts],
            "warnings": list(result.warnings or []),
            "policy_observations": list(result.policy_observations or []),
            "no_side_effects_confirmed": result.no_side_effects_confirmed,
            "config": cfg.diagnostics_view(),
        }

    def _execute_local_cli_backend(self, track: dict[str, Any], context: dict[str, Any], *, backend: str) -> dict[str, Any]:
        if self.cli_runner is None:
            return self._build_runtime_handoff(track, context)
        planning = dict(context.get("planning") or {})
        prompt = str(context.get("prompt") or "").strip()
        model = str(planning.get("model") or "").strip() or None
        timeout = int(((track.get("execution_policy") or {}).get("default_timeout_seconds") or 420))
        started = time.time()
        rc, stdout, stderr, backend_used = self.cli_runner(
            prompt=prompt,
            options=["--no-interaction"],
            timeout=timeout,
            backend=backend,
            model=model,
        )
        return {
            "status": "success" if int(rc) == 0 else "failed",
            "track_id": track.get("id"),
            "requested_backend": backend,
            "backend_used": backend_used,
            "worker_type": track.get("worker_type"),
            "planning_provider": planning.get("provider"),
            "planning_model": model,
            "returncode": int(rc),
            "stdout": str(stdout or ""),
            "stderr_preview": str(stderr or "")[:2000],
            "duration_ms": int((time.time() - started) * 1000),
        }

    def _build_runtime_handoff(self, track: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        planning = dict(context.get("planning") or {})
        return {
            "status": "handoff_required",
            "reason": "track_requires_cli_runner_or_existing_task_runtime_execution",
            "track_id": track.get("id"),
            "requested_backend": track.get("requested_backend"),
            "worker_type": track.get("worker_type"),
            "planning_provider": planning.get("provider"),
            "planning_model": planning.get("model"),
            "execution_provider": track.get("execution_provider"),
            "next_integration_point": "pass cli_runner from TaskScopedExecutionService or CLI backend bridge",
        }


def get_three_worker_track_executor(
    *,
    agent_cfg: dict[str, Any] | None = None,
    cli_runner: CliRunner | None = None,
) -> ThreeWorkerTrackExecutor:
    return ThreeWorkerTrackExecutor(agent_cfg=agent_cfg, cli_runner=cli_runner)
