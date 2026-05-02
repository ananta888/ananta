from __future__ import annotations

from typing import Any


BACKEND_PROVIDER_CONTRACT_VERSION = "v1"

BACKEND_PROVIDER_CONTRACT_SCHEMA: dict[str, Any] = {
    "version": BACKEND_PROVIDER_CONTRACT_VERSION,
    "required_fields": [
        "provider",
        "provider_type",
        "location",
        "transport",
        "capabilities",
        "routing",
        "governance",
        "health",
    ],
    "provider_types": ["local_openai_compatible", "remote_ananta", "hosted_api", "cli_backend", "local_voice_runtime"],
    "locations": ["local", "remote", "hosted"],
}

BACKEND_PROVIDER_CONTRACTS: list[dict[str, Any]] = [
    {
        "provider": "ollama",
        "provider_type": "local_openai_compatible",
        "location": "local",
        "transport": {"protocol": "http", "api_shape": "ollama_generate_or_chat"},
        "capabilities": {"chat": True, "tools": False, "dynamic_models": True, "file_access": False},
        "routing": {"eligible_for_inference": True, "eligible_for_execution": False, "remote_hops": 0},
        "governance": {"trust_level": "local", "requires_remote_hub_policy": False, "audit_required": True},
        "health": {"preflight": "probe_ollama_runtime", "failure_mode": "provider_unavailable"},
    },
    {
        "provider": "lmstudio",
        "provider_type": "local_openai_compatible",
        "location": "local",
        "transport": {"protocol": "http", "api_shape": "openai_compatible"},
        "capabilities": {"chat": True, "tools": False, "dynamic_models": True, "file_access": False},
        "routing": {"eligible_for_inference": True, "eligible_for_execution": False, "remote_hops": 0},
        "governance": {"trust_level": "local", "requires_remote_hub_policy": False, "audit_required": True},
        "health": {"preflight": "probe_lmstudio_runtime", "failure_mode": "provider_unavailable"},
    },
    {
        "provider": "ananta_remote",
        "provider_type": "remote_ananta",
        "location": "remote",
        "transport": {"protocol": "http", "api_shape": "ananta_remote_hub"},
        "capabilities": {"chat": True, "tools": True, "dynamic_models": True, "file_access": "policy_gated"},
        "routing": {"eligible_for_inference": True, "eligible_for_execution": True, "remote_hops": "policy_limited"},
        "governance": {"trust_level": "configured", "requires_remote_hub_policy": True, "audit_required": True},
        "health": {"preflight": "remote_hub_preflight", "failure_mode": "remote_hub_unavailable"},
    },
    {
        "provider": "codex_cli",
        "provider_type": "cli_backend",
        "location": "local",
        "transport": {"protocol": "process", "api_shape": "task_scoped_cli"},
        "capabilities": {"chat": True, "tools": True, "dynamic_models": False, "file_access": "workspace_scoped"},
        "routing": {"eligible_for_inference": False, "eligible_for_execution": True, "remote_hops": 0},
        "governance": {"trust_level": "local_workspace", "requires_remote_hub_policy": False, "audit_required": True},
        "health": {"preflight": "cli_backend_preflight", "failure_mode": "execution_backend_unavailable"},
    },
    {
        "provider": "hosted_openai",
        "provider_type": "hosted_api",
        "location": "hosted",
        "transport": {"protocol": "https", "api_shape": "openai_compatible"},
        "capabilities": {"chat": True, "tools": True, "dynamic_models": False, "file_access": "policy_gated"},
        "routing": {"eligible_for_inference": True, "eligible_for_execution": False, "remote_hops": 0},
        "governance": {"trust_level": "external_api", "requires_remote_hub_policy": False, "audit_required": True},
        "health": {"preflight": "api_key_and_provider_preflight", "failure_mode": "provider_unavailable"},
    },
    {
        "provider": "voice_runtime",
        "provider_type": "local_voice_runtime",
        "location": "local",
        "transport": {"protocol": "http", "api_shape": "ananta_voice_runtime"},
        "capabilities": {
            "chat": False,
            "audio_input": True,
            "transcription": True,
            "voice_command": True,
            "multimodal_audio_prompt": True,
        },
        "routing": {"eligible_for_inference": False, "eligible_for_execution": True, "remote_hops": 0},
        "governance": {"trust_level": "local", "requires_remote_hub_policy": False, "audit_required": True},
        "health": {"preflight": "voice_runtime_health_probe", "failure_mode": "voice_runtime_unavailable"},
    },
]


def build_backend_provider_contract_catalog() -> dict[str, Any]:
    return {
        "version": BACKEND_PROVIDER_CONTRACT_VERSION,
        "schema": dict(BACKEND_PROVIDER_CONTRACT_SCHEMA),
        "contracts": [dict(item) for item in BACKEND_PROVIDER_CONTRACTS],
        "routing_rule": "Local, hosted and remote providers use the same eligibility, governance and health fields before routing decisions.",
    }
