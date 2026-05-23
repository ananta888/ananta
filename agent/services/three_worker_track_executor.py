from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any, Callable

import yaml

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

    def __init__(
        self,
        *,
        agent_cfg: dict[str, Any] | None = None,
        task_scoped_runner: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.agent_cfg = dict(agent_cfg or {})
        self.task_scoped_runner = task_scoped_runner

    def __call__(self, track: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        backend = str(track.get("requested_backend") or track.get("worker_type") or "").strip().lower()
        if backend == "hermes":
            return self._execute_hermes(track, context)
        if backend in {"opencode", "ananta-worker"}:
            return self._execute_task_scoped_track(track, context)
        return {"status": "failed", "reason": f"unsupported_track_backend:{backend or '<empty>'}"}

    def _execute_hermes(self, track: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        cfg = build_hermes_adapter_config_from_agent_config({
            **self.agent_cfg,
            "worker": {"type": "hermes"},
        })
        cfg_ref = self._load_config_ref_overrides(str(track.get("config_ref") or "").strip())
        if cfg_ref:
            cfg = build_hermes_adapter_config_from_agent_config({
                **self.agent_cfg,
                "worker": {"type": "hermes"},
                "hermes_worker_adapter": {
                    **cfg.model_dump(),
                    **cfg_ref,
                },
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
                    sensitivity=ContextSensitivity.project_internal,
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

    def _execute_task_scoped_track(self, track: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if self.task_scoped_runner is not None:
            return self.task_scoped_runner(track, context)
        return self._run_task_scoped_track(track, context)

    def _run_task_scoped_track(self, track: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        from agent.ai_agent import create_app
        from agent.common.sgpt import run_llm_cli_command
        from agent.models import TaskStepExecuteRequest, TaskStepProposeRequest
        from agent.services.task_runtime_service import get_local_task_status, update_local_task_status
        from agent.services.task_scoped_execution_service import get_task_scoped_execution_service
        from agent.tools import registry as tool_registry

        app = create_app()
        planning = dict(context.get("planning") or {})
        backend = str(track.get("requested_backend") or track.get("worker_type") or "").strip().lower()
        prompt = str(context.get("prompt") or "").strip() or "Analyze input and propose next steps."
        run_id = str(context.get("run_id") or "run")
        track_id = str(track.get("id") or backend or "track")
        task_id = f"three-worker-{run_id}-{track_id}-{int(time.time() * 1000)}"
        task_kind = "analysis"

        with app.app_context():
            update_local_task_status(
                task_id,
                "todo",
                force=True,
                title=f"Three worker track {track_id}",
                description=prompt,
                task_kind=task_kind,
                goal_id=f"three-worker:{run_id}",
                required_capabilities=["planning", "coding", "review", "patch_propose"],
                source_task_id=f"three-worker:{run_id}",
                derivation_reason="three_worker_track_executor",
                derivation_depth=0,
            )

            cfg = app.config.get("AGENT_CONFIG", {}) or {}
            old_cfg = copy.deepcopy(cfg)
            try:
                if planning.get("provider"):
                    cfg["default_provider"] = planning.get("provider")
                if planning.get("model"):
                    cfg["default_model"] = planning.get("model")
                sgpt_routing = cfg.get("sgpt_routing") if isinstance(cfg.get("sgpt_routing"), dict) else {}
                backend_map = sgpt_routing.get("task_kind_backend") if isinstance(sgpt_routing.get("task_kind_backend"), dict) else {}
                backend_map = dict(backend_map)
                backend_map[task_kind] = backend
                sgpt_routing = dict(sgpt_routing)
                sgpt_routing["task_kind_backend"] = backend_map
                cfg["sgpt_routing"] = sgpt_routing
                app.config["AGENT_CONFIG"] = cfg

                scoped = get_task_scoped_execution_service()
                propose_request = TaskStepProposeRequest(
                    task_id=task_id,
                    prompt=prompt,
                    provider=planning.get("provider"),
                    model=planning.get("model"),
                )
                propose_out = scoped.propose_task_step(
                    task_id,
                    propose_request,
                    cli_runner=run_llm_cli_command,
                    forwarder=lambda *_args, **_kwargs: None,
                    tool_definitions_resolver=tool_registry.get_tool_definitions,
                )
                if propose_out.status != "success":
                    return {
                        "status": "failed",
                        "reason": f"task_scoped_propose_{propose_out.status}",
                        "track_id": track.get("id"),
                        "requested_backend": track.get("requested_backend"),
                        "worker_type": track.get("worker_type"),
                        "planning_provider": planning.get("provider"),
                        "planning_model": planning.get("model"),
                        "execution_provider": track.get("execution_provider"),
                        "task_id": task_id,
                        "propose": propose_out.data,
                    }

                execute_request = TaskStepExecuteRequest(task_id=task_id, task_kind=task_kind, timeout=180, retries=0)
                execute_out = scoped.execute_task_step(
                    task_id,
                    execute_request,
                    forwarder=lambda *_args, **_kwargs: None,
                    cli_runner=run_llm_cli_command,
                    tool_definitions_resolver=tool_registry.get_tool_definitions,
                )
                task = get_local_task_status(task_id) or {}
                final_status = str((execute_out.data or {}).get("status") or task.get("status") or "").strip().lower()
                normalized = "ok" if final_status == "completed" else "failed"
                return {
                    "status": normalized,
                    "reason": "task_scoped_execution_finished",
                    "track_id": track.get("id"),
                    "requested_backend": track.get("requested_backend"),
                    "worker_type": track.get("worker_type"),
                    "planning_provider": planning.get("provider"),
                    "planning_model": planning.get("model"),
                    "execution_provider": track.get("execution_provider"),
                    "task_id": task_id,
                    "propose": propose_out.data,
                    "execute": execute_out.data,
                    "task_status": final_status,
                }
            finally:
                app.config["AGENT_CONFIG"] = old_cfg

    def _load_config_ref_overrides(self, config_ref: str) -> dict[str, Any]:
        if not config_ref:
            return {}
        path = Path(config_ref)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            return {}

        provider = raw.get("provider") if isinstance(raw.get("provider"), dict) else {}
        models = raw.get("models") if isinstance(raw.get("models"), dict) else {}
        runtime = raw.get("runtime") if isinstance(raw.get("runtime"), dict) else {}
        fallback = raw.get("fallback_policy") if isinstance(raw.get("fallback_policy"), dict) else {}

        primary = models.get("primary") if isinstance(models.get("primary"), dict) else {}
        coding = models.get("coding_fallback") if isinstance(models.get("coding_fallback"), dict) else {}
        cheap = models.get("cheap_fallback") if isinstance(models.get("cheap_fallback"), dict) else {}

        task_kind_models = {
            "plan_only": str(primary.get("model") or "").strip(),
            "review": str(coding.get("model") or "").strip(),
            "analysis": str(coding.get("model") or "").strip(),
            "summarize": str(cheap.get("model") or "").strip(),
            "patch_propose": str(coding.get("model") or "").strip(),
            "research_limited": str(primary.get("model") or "").strip(),
        }
        task_kind_models = {k: v for k, v in task_kind_models.items() if v}

        return {
            "base_url": str(provider.get("base_url") or "").strip() or None,
            "api_key_env": str(provider.get("api_key_env") or "").strip() or None,
            "default_model": str(primary.get("model") or "").strip() or None,
            "timeout_seconds": runtime.get("request_timeout_seconds"),
            "max_retries": runtime.get("api_max_retries"),
            "task_kind_models": task_kind_models or None,
            "fallback_free_models": fallback.get("order"),
        }


def get_three_worker_track_executor(*, agent_cfg: dict[str, Any] | None = None) -> ThreeWorkerTrackExecutor:
    return ThreeWorkerTrackExecutor(agent_cfg=agent_cfg)
