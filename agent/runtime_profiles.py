from __future__ import annotations

import os
from typing import Any


_DEFAULT_PROFILE_NAME = "local-dev"

_RUNTIME_PROFILE_CATALOG: dict[str, dict[str, Any]] = {
    # Product profiles (PRF-080): additiv, ohne bestehende Profile zu entfernen.
    # Diese Profile sind bewusst benannte Defaults fuer typische Nutzungskontexte.
    "demo": {
        "label": "Demo",
        "security_posture": "balanced",
        "recommended_compose_profiles": ["lite"],
        "review_mode": "balanced",
        "default_governance_mode": "balanced",
        "usage_context": "demo",
        "entry_paths": ["ui:first-run", "cli:first-run", "docs:golden-path-ui"],
        "description": "Reproduzierbare Demo- und Erstnutzer-Flows mit klarer Explainability und Golden Paths.",
    },
    "developer-local": {
        "label": "Developer Local",
        "security_posture": "relaxed_local",
        "recommended_compose_profiles": ["lite"],
        "review_mode": "developer_fast_path",
        "default_governance_mode": "safe",
        "usage_context": "trial",
        "entry_paths": ["cli:first-run", "docs:golden-path-cli", "docs:product-profiles"],
        "description": "Schneller lokaler Developer-Loop mit Diagnostics; Policies bleiben sichtbar, aber weniger friktional.",
    },
    "team-controlled": {
        "label": "Team Controlled",
        "security_posture": "strict_compose_defaults",
        "recommended_compose_profiles": ["lite"],
        "review_mode": "policy_enforced",
        "default_governance_mode": "balanced",
        "usage_context": "production",
        "entry_paths": ["ui:dashboard", "docs:release-golden-path", "docs:governance-modes"],
        "description": "Team-Umgebung mit klaren Defaults fuer Policy, Review und Audit; reproduzierbar via Compose.",
    },
    "secure-enterprise": {
        "label": "Secure Enterprise",
        "security_posture": "strict_distributed",
        "recommended_compose_profiles": ["distributed"],
        "review_mode": "governed",
        "default_governance_mode": "strict",
        "usage_context": "production",
        "entry_paths": ["docs:governance-modes", "docs:release-golden-path"],
        "description": "Strikte Governance und minimierte Flaeche fuer kontrollierte Umgebungen mit hoher Audit-Anforderung.",
    },
    "local-first": {
        "label": "Local First",
        "security_posture": "relaxed_local",
        "recommended_compose_profiles": ["lite"],
        "review_mode": "developer_fast_path",
        "default_governance_mode": "safe",
        "usage_context": "trial",
        "entry_paths": ["cli:first-run", "docs:golden-path-cli"],
        "description": "Lokale Ausfuehrung und schnelle Diagnose zuerst; Governance bleibt sichtbar und auditierbar.",
    },
    "review-first": {
        "label": "Review First",
        "security_posture": "strict_compose_defaults",
        "recommended_compose_profiles": ["lite"],
        "review_mode": "review_required",
        "default_governance_mode": "strict",
        "usage_context": "production",
        "entry_paths": ["ui:goal-detail", "docs:governance-modes", "docs:release-golden-path"],
        "description": "Manuelle Kontrolle zuerst: riskante Schritte werden erklaert, reviewbar und auditierbar gemacht.",
    },
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

_RUNTIME_PROFILE_ALIASES = {
    # Friendly aliases for docs/UX and backwards-friendly input.
    "developer local": "developer-local",
    "team controlled": "team-controlled",
    "secure enterprise": "secure-enterprise",
    "dev-local": "developer-local",
    "team": "team-controlled",
    "enterprise": "secure-enterprise",
    "local first": "local-first",
    "review first": "review-first",
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
    requested = _RUNTIME_PROFILE_ALIASES.get(requested, requested)
    source = "config.runtime_profile"

    if not requested:
        env_name = str(os.environ.get("ANANTA_RUNTIME_PROFILE") or "").strip().lower()
        if env_name:
            requested = _RUNTIME_PROFILE_ALIASES.get(env_name, env_name)
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
