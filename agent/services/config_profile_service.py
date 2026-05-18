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
