from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConfigProfile:
    id: str
    description: str
    overrides: dict[str, Any]


_DEFAULT_PROFILES: dict[str, ConfigProfile] = {
    "opencode_preconfigured": ConfigProfile(
        id="opencode_preconfigured",
        description="OpenCode worker with preconfigured default model",
        overrides={
            "sgpt_routing": {"task_kind_backend": {"*": "opencode"}},
            # Conservative profile for Big Pickle + LMStudio planning.
            # Serial planning to avoid contention, bounded timeouts.
            "planning_policy": {
                "timeout_seconds": 900,
                "parallel_goal_planning_max_concurrency": 1,
                "segmented_planning_enabled": True,
                "segment_context_chars": 1800,
                "max_segments": 2,
                "max_output_tokens": 1600,
                "preferred_output_format": "json",
                "selective_repair_rounds": 1,
                "default_runtime_profile": "lmstudio_laptop",
                "runtime_profiles": {
                    "lmstudio_laptop": {
                        "timeout_seconds": 700,
                        "max_output_tokens": 1600,
                        "retry_attempts": 1,
                        "retry_backoff_seconds": 1.0,
                        "segmented_planning_enabled": True,
                        "segment_context_chars": 1400,
                        "max_segments": 2,
                        "preferred_output_format": "json",
                    }
                },
            },
            # Used by adaptive propose timeout resolver as floor when benchmark data is missing.
            "task_propose_timeout_seconds": 420,
        },
    ),
    "opencode_preconfigured_e2e": ConfigProfile(
        id="opencode_preconfigured_e2e",
        description="OpenCode worker profile for fully automated E2E runs (no human review terminal state)",
        overrides={
            "sgpt_routing": {"task_kind_backend": {"*": "opencode"}},
            # E2E must not stall in waiting_for_review.
            "autopilot_task_propose_hard_guard_status": "failed",
            "propose_policy": {
                "allow_human_review": False,
                "on_all_strategies_declined": "failed",
                "autonomous_repair_attempts": 3,
                "autonomous_repair_delay_seconds": 10,
            },
            # Keep planner runs bounded for local LMStudio E2E stability.
            "planning_policy": {
                "timeout_seconds": 540,
                "parallel_goal_planning_max_concurrency": 1,
                "segment_context_chars": 6000,
                "max_output_tokens": 1600,
                "max_segments": 2,
                "default_runtime_profile": "lmstudio_laptop",
                "runtime_profiles": {
                    "lmstudio_laptop": {
                        "timeout_seconds": 700,
                        "max_output_tokens": 1600,
                        "retry_attempts": 1,
                        "retry_backoff_seconds": 1.0,
                        "segmented_planning_enabled": True,
                        "segment_context_chars": 5000,
                        "max_segments": 2,
                        "preferred_output_format": "json",
                    }
                },
                # E2E ramp-up: keep quality checks active but reduce false-negative
                # planning hard-fails for smaller/local models in autonomous runs.
                "validation_profiles": {
                    "new_software_project": {
                        "min_total_tasks": 2,
                        "required_categories": {
                            "infrastructure": 1,
                            "tests": 1,
                        },
                        "max_generic_tasks": 6,
                    }
                },
            },
        },
    ),
    "opencode_ollama_local": ConfigProfile(
        id="opencode_ollama_local",
        description="OpenCode worker using local Ollama model",
        overrides={
            "default_provider": "ollama",
            "default_model": "ananta-default:latest",
            "llm_config": {"base_url": "http://ollama:11434/api/generate", "planner_output_format": "markdown"},
            "opencode_runtime": {"target_provider": "ollama"},
            "sgpt_routing": {"task_kind_backend": {"*": "opencode"}},
        },
    ),
    "ananta_ollama_local": ConfigProfile(
        id="ananta_ollama_local",
        description="Ananta worker using local Ollama model",
        overrides={
            "default_provider": "ollama",
            "default_model": "ananta-default:latest",
            "llm_config": {"base_url": "http://ollama:11434/api/generate", "planner_output_format": "markdown"},
            "sgpt_routing": {"task_kind_backend": {"*": "ananta-worker"}},
        },
    ),
    "ananta_lmstudio_local": ConfigProfile(
        id="ananta_lmstudio_local",
        description="Ananta worker using local LM Studio instance (auto-selects loaded model)",
        overrides={
            "default_provider": "lmstudio",
            "default_model": "auto",
            "llm_config": {"base_url": "http://192.168.178.100:1234/v1", "planner_output_format": "json"},
            "sgpt_routing": {"task_kind_backend": {"*": "ananta-worker"}},
        },
    ),
    "opencode_lmstudio_local": ConfigProfile(
        id="opencode_lmstudio_local",
        description="OpenCode worker using local LM Studio instance (auto-selects loaded model)",
        overrides={
            "default_provider": "lmstudio",
            "default_model": "auto",
            "llm_config": {"base_url": "http://192.168.178.100:1234/v1", "planner_output_format": "json"},
            "opencode_runtime": {"target_provider": "lmstudio"},
            "sgpt_routing": {"task_kind_backend": {"*": "opencode"}},
            # Conservative local-laptop profile: avoid contention and keep strict serial planning.
            "planning_policy": {
                "timeout_seconds": 720,
                "parallel_goal_planning_max_concurrency": 1,
                "segmented_planning_enabled": True,
                "segment_context_chars": 1400,
                "max_segments": 2,
                "max_output_tokens": 1100,
                "preferred_output_format": "json",
                "selective_repair_rounds": 2,
                "default_runtime_profile": "lmstudio_laptop",
                "runtime_profiles": {
                    "lmstudio_laptop": {
                        "timeout_seconds": 540,
                        "max_output_tokens": 1000,
                        "retry_attempts": 1,
                        "retry_backoff_seconds": 1.0,
                        "segmented_planning_enabled": True,
                        "segment_context_chars": 1300,
                        "max_segments": 2,
                        "preferred_output_format": "json",
                    }
                },
            },
            "task_propose_timeout_seconds": 420,
        },
    ),
    "hermes_free_models_preconfigured": ConfigProfile(
        id="hermes_free_models_preconfigured",
        description="Hermes read-only planning/review with free-model routing and OpenCode for mutation tasks",
        overrides={
            "feature_flags": {
                "enable_hermes_worker_adapter": True,
            },
            "hermes_worker_adapter": {
                "enabled": True,
                "feature_flag_enabled": True,
                "base_url": "http://192.168.178.100:1234/v1",
                "cloud_allowed": True,
                "strict_json_required": True,
                "timeout_seconds": 120,
                "max_retries": 1,
                "max_context_chars": 12000,
                "default_temperature": 0.1,
                "allowed_task_kinds": ["plan_only", "review", "summarize", "patch_propose", "research_limited"],
                "blocked_task_kinds": ["patch_apply", "command_execute", "shell_execute", "shell_execution", "service_mutation", "config_mutation"],
                "default_model": "z-ai/glm-4.5-air:free",
                "task_kind_models": {
                    "plan_only": "z-ai/glm-4.5-air:free",
                    "summarize": "z-ai/glm-4.5-air:free",
                    "review": "qwen/qwen3-coder:free",
                    "patch_propose": "qwen/qwen3-coder:free",
                    "research_limited": "z-ai/glm-4.5-air:free",
                },
                "fallback_free_models": {
                    "plan_only": ["z-ai/glm-4.5-air:free", "moonshotai/kimi-k2:free"],
                    "review": ["qwen/qwen3-coder:free"],
                    "patch_propose": ["qwen/qwen3-coder:free"],
                    "default": ["moonshotai/kimi-k2:free"],
                },
                "model_selection_policy": {
                    "prefer_task_specific_model": True,
                    "require_free_model_suffix": True,
                    "allow_fallback_on_unavailable": True,
                    "reject_blocked_models": True,
                    "reject_mutation_tasks_for_hermes": True,
                    "allow_candidate_roles": True,
                },
            },
            "sgpt_routing": {
                "task_kind_backend": {
                    "analysis": "hermes",
                    "review": "hermes",
                    "doc": "hermes",
                    "research": "hermes",
                    "coding": "opencode",
                    "ops": "opencode",
                    "testing": "opencode",
                }
            },
                "planning_policy": {
                    "timeout_seconds": 700,
                    "max_output_tokens": 3000,
                    "segmented_planning_enabled": True,
                    "segment_context_chars": 2400,
                    "max_segments": 3,
                    "preferred_output_format": "json",
                    "selective_repair_rounds": 2,
                    "learning_loop": {
                        "enabled": False,
                        "interval_seconds": 900,
                        "lookback_runs": 120,
                        "min_runs": 8,
                        "min_failures": 3,
                        "min_parse_success_rate": 0.7,
                        "min_validation_success_rate": 0.7,
                        "min_materialization_success_rate": 0.6,
                        "max_repair_rate": 0.4,
                        "candidate_activation_threshold": 0.75,
                        "rollback_threshold": 0.55,
                        "freeze_minutes": 120,
                        "canary_window_runs": 10,
                        "auto_activate": False,
                        "require_review_before_activate": True,
                    },
                    "validation_profiles": {
                        "new_software_project": {
                        "min_total_tasks": 1,
                        "required_categories": {
                            "infrastructure": 1,
                        },
                        "max_generic_tasks": 2,
                    },
                },
                "runtime_profiles": {
                    "lmstudio_laptop": {
                        "timeout_seconds": 700,
                        "max_output_tokens": 3000,
                        "retry_attempts": 1,
                        "retry_backoff_seconds": 1.0,
                        "segmented_planning_enabled": True,
                        "segment_context_chars": 2200,
                        "max_segments": 3,
                        "preferred_output_format": "json",
                    }
                },
                "default_runtime_profile": "lmstudio_laptop",
            },
        },
    ),
}


class ConfigProfileService:
    def list_profiles(self) -> list[dict[str, Any]]:
        return [
            {
                "id": profile.id,
                "description": profile.description,
                "overrides": dict(profile.overrides),
            }
            for profile in _DEFAULT_PROFILES.values()
        ]

    def get_profile(self, profile_id: str | None) -> dict[str, Any] | None:
        key = str(profile_id or "").strip()
        if not key:
            return None
        profile = _DEFAULT_PROFILES.get(key)
        if profile is None:
            return None
        return {
            "id": profile.id,
            "description": profile.description,
            "overrides": dict(profile.overrides),
        }

    def validate_profile_availability(
        self,
        profile_id: str | None,
        *,
        agent_config: dict[str, Any] | None = None,
        provider_urls: dict[str, Any] | None = None,
        block_on_unavailable: bool = False,
    ) -> dict[str, Any]:
        """CPR-002: Check whether the providers required by this profile are reachable.

        Returns a dict with:
          - validation_level: "structural_valid" | "provider_observable" | "provider_unavailable"
          - warnings: list of warning strings
          - errors: list of error strings (non-empty only when block_on_unavailable=True and provider is down)
          - provider_snapshots: dict of per-provider probe results (may be empty on timeout/error)
        """
        from agent.services.provider_observer_service import get_provider_observer_service

        profile = self.get_profile(profile_id)
        if profile is None:
            return {
                "validation_level": "structural_valid",
                "warnings": [],
                "errors": [],
                "provider_snapshots": {},
            }

        overrides = profile.get("overrides") or {}
        required_provider = str(overrides.get("default_provider") or "").strip().lower() or None
        warnings: list[str] = []
        errors: list[str] = []
        provider_snapshots: dict[str, Any] = {}

        if required_provider:
            try:
                snapshot = get_provider_observer_service().snapshot(
                    agent_config=agent_config or {},
                    provider_urls=provider_urls or {},
                )
                providers_state = snapshot.get("providers") or {}
                probe = providers_state.get(required_provider) or {}
                provider_snapshots[required_provider] = probe
                runtime = probe.get("runtime") or {}
                is_ok = bool(runtime.get("ok"))
                if not is_ok:
                    msg = f"profile '{profile_id}' requires provider '{required_provider}' which is currently unreachable"
                    if block_on_unavailable:
                        errors.append(msg)
                    else:
                        warnings.append(msg)
                    return {
                        "validation_level": "provider_unavailable",
                        "warnings": warnings,
                        "errors": errors,
                        "provider_snapshots": provider_snapshots,
                    }
            except Exception as exc:
                warnings.append(f"provider availability check failed for '{required_provider}': {str(exc)[:120]}")

        return {
            "validation_level": "provider_observable" if required_provider else "structural_valid",
            "warnings": warnings,
            "errors": errors,
            "provider_snapshots": provider_snapshots,
        }


_SERVICE = ConfigProfileService()


def get_config_profile_service() -> ConfigProfileService:
    return _SERVICE
