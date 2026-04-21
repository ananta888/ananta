from __future__ import annotations

from typing import Any

from agent.governance_modes import resolve_governance_mode
from agent.runtime_profiles import resolve_runtime_profile


def build_effective_policy_profile(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(config or {})
    governance = resolve_governance_mode(cfg)
    runtime = resolve_runtime_profile(cfg)
    review_policy = dict(cfg.get("review_policy") or {})
    execution_risk_policy = dict(cfg.get("execution_risk_policy") or {})
    exposure_policy = dict(cfg.get("exposure_policy") or {})
    terminal_policy = dict(cfg.get("terminal_policy") or {})
    action_packs = dict(cfg.get("action_packs") or {})

    governance_mode = str(governance.get("effective") or "balanced")
    runtime_profile = str(runtime.get("effective") or "local-dev")
    mode = dict(governance.get("mode") or {})
    profile = dict(runtime.get("profile") or {})

    review_enabled = bool(review_policy.get("enabled", True))
    min_review = str(review_policy.get("min_risk_level_for_review") or "high")
    terminal_risk = str(review_policy.get("terminal_risk_level") or "high")
    file_risk = str(review_policy.get("file_access_risk_level") or "medium")

    return {
        "version": "v1",
        "summary": (
            f"{mode.get('label') or governance_mode} governance on "
            f"{profile.get('label') or runtime_profile}: review {'enabled' if review_enabled else 'disabled'}, "
            f"minimum review risk {min_review}."
        ),
        "governance_mode": governance,
        "runtime_profile": runtime,
        "controls": {
            "review": {
                "enabled": review_enabled,
                "min_risk_level_for_review": min_review,
                "terminal_risk_level": terminal_risk,
                "file_access_risk_level": file_risk,
            },
            "execution_risk": {
                "enabled": bool(execution_risk_policy.get("enabled", True)),
                "default_action": str(execution_risk_policy.get("default_action") or "deny"),
                "review_required_for": list(execution_risk_policy.get("review_required_for") or []),
            },
            "terminal": {
                "enabled": bool(terminal_policy.get("enabled", True)),
                "allow_interactive": bool(terminal_policy.get("allow_interactive", False)),
            },
            "exposure": {
                "openai_compat_enabled": bool((exposure_policy.get("openai_compat") or {}).get("enabled", False)),
                "mcp_enabled": bool((exposure_policy.get("mcp") or {}).get("enabled", False)),
            },
            "action_packs": {
                "enabled": bool(action_packs.get("enabled", True)),
                "default_mode": str(action_packs.get("default_mode") or "balanced"),
            },
        },
        "export_hints": [
            "Use this profile as the readable audit/control summary for operators.",
            "Keep detailed policy enforcement in the dedicated policy blocks; this profile is a read model.",
        ],
    }
