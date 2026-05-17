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
            "llm_config": {"base_url": "http://ollama:11434/api/generate"},
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
            "llm_config": {"base_url": "http://ollama:11434/api/generate"},
            "sgpt_routing": {"task_kind_backend": {"*": "ananta-worker"}},
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


_SERVICE = ConfigProfileService()


def get_config_profile_service() -> ConfigProfileService:
    return _SERVICE
