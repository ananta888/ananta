from __future__ import annotations

import os
from typing import Any


_DEFAULT_PROFILE_NAME = "local-dev"

_RUNTIME_PROFILE_CATALOG: dict[str, dict[str, Any]] = {
    "local-dev": {
        "label": "Local Development",
        "security_posture": "relaxed_local",
        "recommended_compose_profiles": ["lite"],
        "review_mode": "developer_fast_path",
        "description": "Single-machine developer workflow with local diagnostics enabled.",
    },
    "trusted-lab": {
        "label": "Trusted Lab",
        "security_posture": "balanced",
        "recommended_compose_profiles": ["llm", "lite"],
        "review_mode": "balanced",
        "description": "Shared internal environment with explicit safeguards and moderate velocity.",
    },
    "compose-safe": {
        "label": "Compose Safe",
        "security_posture": "strict_compose_defaults",
        "recommended_compose_profiles": ["lite"],
        "review_mode": "policy_enforced",
        "description": "Default docker compose setup for team development and repeatable local test runs.",
    },
    "distributed-strict": {
        "label": "Distributed Strict",
        "security_posture": "strict_distributed",
        "recommended_compose_profiles": ["distributed"],
        "review_mode": "governed",
        "description": "Multi-node distributed deployment with strict routing and governance expectations.",
    },
}


def runtime_profile_catalog() -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in _RUNTIME_PROFILE_CATALOG.items()}


def _infer_profile_from_compose_env() -> tuple[str, str]:
    compose_profiles = str(os.environ.get("COMPOSE_PROFILES") or "").strip().lower()
    if "distributed" in compose_profiles:
        return "distributed-strict", "env.COMPOSE_PROFILES"
    if compose_profiles:
        return "compose-safe", "env.COMPOSE_PROFILES"
    return "compose-safe", "heuristic.compose_default"


def resolve_runtime_profile(config: dict | None = None) -> dict[str, Any]:
    cfg = dict(config or {})
    catalog = runtime_profile_catalog()
    requested = str(cfg.get("runtime_profile") or "").strip().lower()
    source = "config.runtime_profile"

    if not requested:
        env_name = str(os.environ.get("ANANTA_RUNTIME_PROFILE") or "").strip().lower()
        if env_name:
            requested = env_name
            source = "env.ANANTA_RUNTIME_PROFILE"
        else:
            requested, source = _infer_profile_from_compose_env()

    valid = requested in catalog
    effective = requested if valid else _DEFAULT_PROFILE_NAME
    validation_status = "ok" if valid else "error"
    validation_message = None if valid else f"invalid_runtime_profile:{requested or '<empty>'}"

    return {
        "requested": requested,
        "effective": effective,
        "valid": valid,
        "source": source,
        "catalog": catalog,
        "profile": dict(catalog.get(effective) or {}),
        "validation": {"status": validation_status, "message": validation_message},
    }
