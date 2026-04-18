from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


PLATFORM_MODES = ("local-dev", "trusted-internal", "admin-only", "semi-public")
_DEFAULT_PLATFORM_MODE = "local-dev"

_PLATFORM_MODE_ALIASES = {
    "local": "local-dev",
    "dev": "local-dev",
    "trusted-lab": "trusted-internal",
    "internal": "trusted-internal",
    "admin": "admin-only",
    "public": "semi-public",
}

_BASE_EXPOSURE_POLICY: dict[str, Any] = {
    "openai_compat": {
        "enabled": True,
        "allow_agent_auth": True,
        "allow_user_auth": True,
        "require_admin_for_user_auth": True,
        "allow_files_api": True,
        "emit_audit_events": True,
        "instance_id": None,
        "max_hops": 3,
    },
    "mcp": {
        "enabled": False,
        "allow_agent_auth": False,
        "allow_user_auth": False,
        "require_admin_for_user_auth": True,
        "emit_audit_events": True,
    },
    "remote_hubs": {
        "enabled": True,
        "require_admin_for_user_auth": True,
        "emit_audit_events": True,
        "max_hops": 3,
    },
}

_MODE_POLICIES: dict[str, dict[str, Any]] = {
    "local-dev": {
        "exposure_policy": deepcopy(_BASE_EXPOSURE_POLICY),
        "terminal_policy": {
            "enabled": False,
            "allow_read": False,
            "allow_interactive": False,
            "require_admin": True,
            "emit_audit_events": True,
        },
    },
    "trusted-internal": {
        "exposure_policy": {
            "openai_compat": {
                **_BASE_EXPOSURE_POLICY["openai_compat"],
                "require_admin_for_user_auth": True,
            },
            "mcp": {
                **_BASE_EXPOSURE_POLICY["mcp"],
                "enabled": True,
                "allow_agent_auth": True,
                "allow_user_auth": True,
            },
            "remote_hubs": {
                **_BASE_EXPOSURE_POLICY["remote_hubs"],
                "enabled": True,
            },
        },
        "terminal_policy": {
            "enabled": False,
            "allow_read": True,
            "allow_interactive": False,
            "require_admin": True,
            "emit_audit_events": True,
        },
    },
    "admin-only": {
        "exposure_policy": {
            "openai_compat": {
                **_BASE_EXPOSURE_POLICY["openai_compat"],
                "allow_user_auth": True,
                "require_admin_for_user_auth": True,
            },
            "mcp": {
                **_BASE_EXPOSURE_POLICY["mcp"],
                "enabled": True,
                "allow_agent_auth": True,
                "allow_user_auth": True,
                "require_admin_for_user_auth": True,
            },
            "remote_hubs": {
                **_BASE_EXPOSURE_POLICY["remote_hubs"],
                "enabled": True,
                "require_admin_for_user_auth": True,
            },
        },
        "terminal_policy": {
            "enabled": False,
            "allow_read": True,
            "allow_interactive": True,
            "require_admin": True,
            "emit_audit_events": True,
        },
    },
    "semi-public": {
        "exposure_policy": {
            "openai_compat": {
                **_BASE_EXPOSURE_POLICY["openai_compat"],
                "allow_agent_auth": False,
                "allow_user_auth": True,
                "require_admin_for_user_auth": True,
                "allow_files_api": False,
                "max_hops": 1,
            },
            "mcp": {
                **_BASE_EXPOSURE_POLICY["mcp"],
                "enabled": False,
                "allow_agent_auth": False,
                "allow_user_auth": False,
            },
            "remote_hubs": {
                **_BASE_EXPOSURE_POLICY["remote_hubs"],
                "enabled": False,
                "max_hops": 1,
            },
        },
        "terminal_policy": {
            "enabled": False,
            "allow_read": False,
            "allow_interactive": False,
            "require_admin": True,
            "emit_audit_events": True,
        },
    },
}


@dataclass(frozen=True)
class TerminalAccessDecision:
    allowed: bool
    reason: str
    mode: str
    platform_mode: str
    policy: dict[str, Any]


def _merge_dict(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    merged = deepcopy(base)
    if not isinstance(override, dict):
        return merged
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _is_legacy_base_exposure_policy(raw: dict[str, Any]) -> bool:
    if not isinstance(raw, dict):
        return False
    normalized = _merge_dict(_BASE_EXPOSURE_POLICY, raw)
    return normalized == _BASE_EXPOSURE_POLICY


class PlatformGovernanceService:
    """Resolves platform-mode policy without taking over hub orchestration."""

    def normalize_platform_mode(self, raw: Any) -> str:
        requested = str(raw or "").strip().lower()
        requested = _PLATFORM_MODE_ALIASES.get(requested, requested)
        return requested if requested in PLATFORM_MODES else _DEFAULT_PLATFORM_MODE

    def is_supported_platform_mode(self, raw: Any) -> bool:
        requested = str(raw or "").strip().lower()
        if not requested:
            return True
        return requested in PLATFORM_MODES or requested in _PLATFORM_MODE_ALIASES

    def resolve_platform_mode(self, cfg: dict[str, Any] | None) -> str:
        cfg = cfg if isinstance(cfg, dict) else {}
        governance_cfg = cfg.get("governance") if isinstance(cfg.get("governance"), dict) else {}
        return self.normalize_platform_mode(governance_cfg.get("platform_mode") or cfg.get("platform_mode"))

    def resolve_exposure_policy(self, cfg: dict[str, Any] | None) -> dict[str, Any]:
        cfg = cfg if isinstance(cfg, dict) else {}
        mode = self.resolve_platform_mode(cfg)
        mode_policy = _MODE_POLICIES[mode]["exposure_policy"]
        exposure_override = cfg.get("exposure_policy") if isinstance(cfg.get("exposure_policy"), dict) else {}
        if mode != "local-dev" and _is_legacy_base_exposure_policy(exposure_override):
            exposure_override = {}
        return _merge_dict(mode_policy, exposure_override)

    def resolve_terminal_policy(self, cfg: dict[str, Any] | None) -> dict[str, Any]:
        cfg = cfg if isinstance(cfg, dict) else {}
        mode = self.resolve_platform_mode(cfg)
        mode_policy = _MODE_POLICIES[mode]["terminal_policy"]
        return _merge_dict(mode_policy, cfg.get("terminal_policy") if isinstance(cfg.get("terminal_policy"), dict) else {})

    def evaluate_terminal_access(
        self,
        *,
        cfg: dict[str, Any] | None,
        terminal_mode: str,
        is_admin: bool,
    ) -> TerminalAccessDecision:
        platform_mode = self.resolve_platform_mode(cfg)
        policy = self.resolve_terminal_policy(cfg)
        mode = terminal_mode if terminal_mode in {"interactive", "read"} else "interactive"
        if not bool(policy.get("enabled", False)):
            return TerminalAccessDecision(False, "terminal_disabled", mode, platform_mode, policy)
        if bool(policy.get("require_admin", True)) and not is_admin:
            return TerminalAccessDecision(False, "terminal_admin_required", mode, platform_mode, policy)
        if mode == "read" and not bool(policy.get("allow_read", False)):
            return TerminalAccessDecision(False, "terminal_read_disabled", mode, platform_mode, policy)
        if mode == "interactive" and not bool(policy.get("allow_interactive", False)):
            return TerminalAccessDecision(False, "terminal_interactive_disabled", mode, platform_mode, policy)
        return TerminalAccessDecision(True, "ok", mode, platform_mode, policy)

    def build_policy_read_model(self, cfg: dict[str, Any] | None) -> dict[str, Any]:
        mode = self.resolve_platform_mode(cfg)
        exposure_policy = self.resolve_exposure_policy(cfg)
        terminal_policy = self.resolve_terminal_policy(cfg)
        return {
            "policy_version": "platform-governance-v1",
            "platform_mode": mode,
            "available_modes": list(PLATFORM_MODES),
            "exposure_policy": exposure_policy,
            "terminal_policy": terminal_policy,
            "decisions": {
                "openai_compat": {
                    "allowed": bool(exposure_policy.get("openai_compat", {}).get("enabled", False)),
                    "reason": "configured_by_platform_mode",
                },
                "mcp": {
                    "allowed": bool(exposure_policy.get("mcp", {}).get("enabled", False)),
                    "reason": "configured_by_platform_mode",
                },
                "remote_hubs": {
                    "allowed": bool(exposure_policy.get("remote_hubs", {}).get("enabled", False)),
                    "reason": "configured_by_platform_mode",
                },
                "terminal_read": {
                    "allowed": bool(terminal_policy.get("enabled", False) and terminal_policy.get("allow_read", False)),
                    "reason": "configured_by_platform_mode",
                },
                "terminal_interactive": {
                    "allowed": bool(
                        terminal_policy.get("enabled", False) and terminal_policy.get("allow_interactive", False)
                    ),
                    "reason": "configured_by_platform_mode",
                },
            },
        }


platform_governance_service = PlatformGovernanceService()


def get_platform_governance_service() -> PlatformGovernanceService:
    return platform_governance_service
