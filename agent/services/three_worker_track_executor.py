from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

import requests
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
        planning = dict(context.get("planning") or {})
        backend = str(track.get("requested_backend") or track.get("worker_type") or "").strip().lower()
        prompt = str(context.get("prompt") or "").strip() or "Analyze input and propose next steps."
        run_id = str(context.get("run_id") or "run")
        track_id = str(track.get("id") or backend or "track")
        task_kind = "analysis"
        base_url = str(os.getenv("ANANTA_BASE_URL", "http://localhost:5000")).rstrip("/")
        user = str(os.getenv("ANANTA_USER", "admin")).strip() or "admin"
        password = str(os.getenv("ANANTA_PASSWORD") or os.getenv("INITIAL_ADMIN_PASSWORD") or "").strip()
        if not password:
            return {
                "status": "failed",
                "reason": "missing_ananta_password_env",
                "track_id": track.get("id"),
                "requested_backend": track.get("requested_backend"),
                "worker_type": track.get("worker_type"),
            }

        session = requests.Session()
        login_res = session.post(
            f"{base_url}/login",
            json={"username": user, "password": password},
            timeout=20,
        )
        login_res.raise_for_status()
        token = ((login_res.json().get("data") or {}).get("access_token") or "").strip()
        if not token:
            return {
                "status": "failed",
                "reason": "login_missing_access_token",
                "track_id": track.get("id"),
                "requested_backend": track.get("requested_backend"),
                "worker_type": track.get("worker_type"),
            }
        headers = {"Authorization": f"Bearer {token}"}

        cfg_before = session.get(f"{base_url}/config", headers=headers, timeout=20)
        cfg_before.raise_for_status()
        cfg_payload = dict((cfg_before.json().get("data") or {}))
        sgpt_routing = cfg_payload.get("sgpt_routing") if isinstance(cfg_payload.get("sgpt_routing"), dict) else {}
        backend_map = sgpt_routing.get("task_kind_backend") if isinstance(sgpt_routing.get("task_kind_backend"), dict) else {}
        backend_map = dict(backend_map)
        backend_map[task_kind] = backend

        config_patch = {
            "default_provider": planning.get("provider"),
            "default_model": planning.get("model"),
            "sgpt_routing": {
                **dict(sgpt_routing),
                "task_kind_backend": backend_map,
            },
        }
        llm_base = planning.get("base_url")
        if planning.get("provider") and planning.get("model") and llm_base:
            config_patch["llm_config"] = {
                "provider": planning.get("provider"),
                "model": planning.get("model"),
                "base_url": llm_base,
                "lmstudio_api_mode": "chat" if str(planning.get("provider")).lower() == "lmstudio" else None,
            }
        config_patch["llm_config"] = {
            k: v for k, v in dict(config_patch.get("llm_config") or {}).items() if v is not None
        } or cfg_payload.get("llm_config")

        session.post(f"{base_url}/config", headers=headers, json=config_patch, timeout=30).raise_for_status()

        task_id = None
        try:
            task_create = session.post(
                f"{base_url}/tasks",
                headers=headers,
                json={
                    "title": f"Three worker track {track_id}",
                    "description": prompt,
                    "task_kind": task_kind,
                    "status": "todo",
                    "goal_id": f"three-worker:{run_id}",
                    "source": "three_worker_track_executor",
                    "created_by": "three_worker_runner",
                },
                timeout=30,
            )
            task_create.raise_for_status()
            task_id = str(((task_create.json().get("data") or {}).get("id") or "")).strip()
            if not task_id:
                raise RuntimeError("task_create_missing_id")

            propose_res = session.post(
                f"{base_url}/tasks/{task_id}/step/propose",
                headers=headers,
                json={
                    "task_id": task_id,
                    "prompt": prompt,
                    "provider": planning.get("provider"),
                    "model": planning.get("model"),
                },
                timeout=120,
            )
            propose_res.raise_for_status()
            propose_data = dict((propose_res.json().get("data") or {}))

            execute_res = session.post(
                f"{base_url}/tasks/{task_id}/step/execute",
                headers=headers,
                json={
                    "task_id": task_id,
                    "task_kind": task_kind,
                    "timeout": 180,
                    "retries": 0,
                },
                timeout=240,
            )
            execute_res.raise_for_status()
            execute_data = dict((execute_res.json().get("data") or {}))
            final_status = str(execute_data.get("status") or "").strip().lower()
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
                "propose": propose_data,
                "execute": execute_data,
                "task_status": final_status,
            }
        finally:
            restore_patch = {
                "default_provider": cfg_payload.get("default_provider"),
                "default_model": cfg_payload.get("default_model"),
                "llm_config": cfg_payload.get("llm_config"),
                "sgpt_routing": cfg_payload.get("sgpt_routing"),
            }
            try:
                session.post(f"{base_url}/config", headers=headers, json=restore_patch, timeout=30)
            except Exception:
                pass

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
