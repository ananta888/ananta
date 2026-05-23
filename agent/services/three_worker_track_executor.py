from __future__ import annotations

from typing import Any

from worker.core.context_resolver import ContextBlock, ContextSensitivity
from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope, ModelPolicy
from worker.core.hermes_adapter import HermesAdapter
from worker.core.hermes_default_config import build_hermes_adapter_config_from_agent_config


class ThreeWorkerTrackExecutor:
    """Execute one track of the three-worker comparison run.

    Hermes is wired directly to the governed HermesAdapter.
    OpenCode and ananta-worker are routed as explicit handoff descriptors for the
    existing task/propose runtime layer; they are not faked as successful work.
    """

    def __init__(self, *, agent_cfg: dict[str, Any] | None = None) -> None:
        self.agent_cfg = dict(agent_cfg or {})

    def __call__(self, track: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        backend = str(track.get("requested_backend") or track.get("worker_type") or "").strip().lower()
        if backend == "hermes":
            return self._execute_hermes(track, context)
        if backend in {"opencode", "ananta-worker"}:
            return self._build_runtime_handoff(track, context)
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

    def _build_runtime_handoff(self, track: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        planning = dict(context.get("planning") or {})
        return {
            "status": "handoff_required",
            "reason": "track_requires_existing_task_runtime_execution",
            "track_id": track.get("id"),
            "requested_backend": track.get("requested_backend"),
            "worker_type": track.get("worker_type"),
            "planning_provider": planning.get("provider"),
            "planning_model": planning.get("model"),
            "execution_provider": track.get("execution_provider"),
            "next_integration_point": "TaskScopedExecutionService.propose_task_step / execute_task_step",
        }


def get_three_worker_track_executor(*, agent_cfg: dict[str, Any] | None = None) -> ThreeWorkerTrackExecutor:
    return ThreeWorkerTrackExecutor(agent_cfg=agent_cfg)
