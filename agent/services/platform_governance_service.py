from __future__ import annotations

import ipaddress
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, List, Optional


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
    "voice": {
        "enabled": True,
        "allow_agent_auth": False,
        "allow_user_auth": True,
        "require_admin_for_user_auth": False,
        "require_explicit_approval_for_goal": True,
        "emit_audit_events": True,
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
            "max_session_seconds": 1800,
            "idle_timeout_seconds": 300,
            "input_preview_max_chars": 120,
            "allowed_roles": [],
            "allowed_cidrs": [],
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
            "voice": {
                **_BASE_EXPOSURE_POLICY["voice"],
                "enabled": True,
                "require_admin_for_user_auth": False,
            },
        },
        "terminal_policy": {
            "enabled": False,
            "allow_read": True,
            "allow_interactive": False,
            "require_admin": True,
            "emit_audit_events": True,
            "max_session_seconds": 1800,
            "idle_timeout_seconds": 300,
            "input_preview_max_chars": 120,
            "allowed_roles": [],
            "allowed_cidrs": [],
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
            "voice": {
                **_BASE_EXPOSURE_POLICY["voice"],
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
            "max_session_seconds": 1800,
            "idle_timeout_seconds": 300,
            "input_preview_max_chars": 120,
            "allowed_roles": [],
            "allowed_cidrs": [],
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
            "voice": {
                **_BASE_EXPOSURE_POLICY["voice"],
                "enabled": False,
                "allow_user_auth": False,
            },
        },
        "terminal_policy": {
            "enabled": False,
            "allow_read": False,
            "allow_interactive": False,
            "require_admin": True,
            "emit_audit_events": True,
            "max_session_seconds": 600,
            "idle_timeout_seconds": 120,
            "input_preview_max_chars": 80,
            "allowed_roles": [],
            "allowed_cidrs": [],
        },
    },
}


_DEFAULT_ACTION_PACKS: dict[str, dict[str, Any]] = {
    "file": {
        "description": "Datei-Operationen (Lesen, Schreiben, Patchen)",
        "capabilities": ["file_read", "file_write", "file_patch"],
        "enabled_by_default": True,
    },
    "git": {
        "description": "Git-Operationen (Status, Diff, Commit)",
        "capabilities": ["git_status", "git_diff", "git_commit"],
        "enabled_by_default": True,
    },
    "shell": {
        "description": "Shell-Kommandoausfuehrung (Eingeschraenkt)",
        "capabilities": ["shell_exec"],
        "enabled_by_default": False,
    },
    "browser": {
        "description": "Web-Recherche und Seitenabruf",
        "capabilities": ["web_search", "web_fetch"],
        "enabled_by_default": False,
    },
    "document": {
        "description": "Dokumenten-Extraktion und Umwandlung",
        "capabilities": ["doc_extract", "doc_convert"],
        "enabled_by_default": True,
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


def _positive_int(raw: Any, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _normalize_string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item or "").strip() for item in raw if str(item or "").strip()]


def _remote_addr_matches_cidrs(remote_addr: str | None, cidrs: list[str]) -> bool:
    if not cidrs:
        return True
    if not remote_addr:
        return False
    try:
        address = ipaddress.ip_address(str(remote_addr).strip())
    except ValueError:
        return False
    for raw_cidr in cidrs:
        try:
            if address in ipaddress.ip_network(raw_cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


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
        raw_policy = _merge_dict(mode_policy, cfg.get("terminal_policy") if isinstance(cfg.get("terminal_policy"), dict) else {})
        return self.normalize_terminal_policy(raw_policy)

    def normalize_terminal_policy(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        return {
            "enabled": bool(raw.get("enabled", False)),
            "allow_read": bool(raw.get("allow_read", False)),
            "allow_interactive": bool(raw.get("allow_interactive", False)),
            "require_admin": bool(raw.get("require_admin", True)),
            "emit_audit_events": bool(raw.get("emit_audit_events", True)),
            "max_session_seconds": _positive_int(raw.get("max_session_seconds"), 1800, minimum=30, maximum=86400),
            "idle_timeout_seconds": _positive_int(raw.get("idle_timeout_seconds"), 300, minimum=10, maximum=86400),
            "input_preview_max_chars": _positive_int(raw.get("input_preview_max_chars"), 120, minimum=0, maximum=1000),
            "allowed_roles": _normalize_string_list(raw.get("allowed_roles")),
            "allowed_cidrs": _normalize_string_list(raw.get("allowed_cidrs")),
        }

    def evaluate_terminal_access(
        self,
        *,
        cfg: dict[str, Any] | None,
        terminal_mode: str,
        is_admin: bool,
        roles: list[str] | None = None,
        remote_addr: str | None = None,
    ) -> TerminalAccessDecision:
        platform_mode = self.resolve_platform_mode(cfg)
        policy = self.resolve_terminal_policy(cfg)
        mode = terminal_mode if terminal_mode in {"interactive", "read"} else "interactive"
        if not bool(policy.get("enabled", False)):
            return TerminalAccessDecision(False, "terminal_disabled", mode, platform_mode, policy)
        if bool(policy.get("require_admin", True)) and not is_admin:
            return TerminalAccessDecision(False, "terminal_admin_required", mode, platform_mode, policy)
        allowed_roles = {str(item or "").strip().lower() for item in policy.get("allowed_roles", []) if str(item or "").strip()}
        if allowed_roles:
            actual_roles = {str(item or "").strip().lower() for item in (roles or []) if str(item or "").strip()}
            if actual_roles.isdisjoint(allowed_roles):
                return TerminalAccessDecision(False, "terminal_role_not_allowed", mode, platform_mode, policy)
        allowed_cidrs = [str(item or "").strip() for item in policy.get("allowed_cidrs", []) if str(item or "").strip()]
        if not _remote_addr_matches_cidrs(remote_addr, allowed_cidrs):
            return TerminalAccessDecision(False, "terminal_network_not_allowed", mode, platform_mode, policy)
        if mode == "read" and not bool(policy.get("allow_read", False)):
            return TerminalAccessDecision(False, "terminal_read_disabled", mode, platform_mode, policy)
        if mode == "interactive" and not bool(policy.get("allow_interactive", False)):
            return TerminalAccessDecision(False, "terminal_interactive_disabled", mode, platform_mode, policy)
        return TerminalAccessDecision(True, "ok", mode, platform_mode, policy)

    def build_policy_read_model(self, cfg: dict[str, Any] | None) -> dict[str, Any]:
        mode = self.resolve_platform_mode(cfg)
        exposure_policy = self.resolve_exposure_policy(cfg)
        terminal_policy = self.resolve_terminal_policy(cfg)
        action_packs = self.resolve_action_packs(cfg)
        return {
            "policy_version": "platform-governance-v1",
            "platform_mode": mode,
            "available_modes": list(PLATFORM_MODES),
            "exposure_policy": exposure_policy,
            "terminal_policy": terminal_policy,
            "action_packs": action_packs,
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
                **{
                    f"action_pack_{p['name']}": {
                        "allowed": p["enabled"],
                        "reason": "configured_by_action_pack_policy"
                    } for p in action_packs
                }
            },
        }

    def resolve_action_packs(self, cfg: dict[str, Any] | None) -> List[dict[str, Any]]:
        cfg = cfg if isinstance(cfg, dict) else {}
        action_packs_cfg = cfg.get("action_packs") if isinstance(cfg.get("action_packs"), dict) else {}

        resolved = []
        for name, defaults in _DEFAULT_ACTION_PACKS.items():
            override = action_packs_cfg.get(name) or {}
            enabled = bool(override.get("enabled", defaults["enabled_by_default"]))
            resolved.append({
                "name": name,
                "description": defaults["description"],
                "capabilities": defaults["capabilities"],
                "enabled": enabled,
                "source": "default" if not override else "config"
            })
        return resolved

    def evaluate_action_pack_access(self, action_pack_name: str, cfg: dict[str, Any] | None) -> bool:
        packs = self.resolve_action_packs(cfg)
        for pack in packs:
            if pack["name"] == action_pack_name:
                return pack["enabled"]
        return False


platform_governance_service = PlatformGovernanceService()


def get_platform_governance_service() -> PlatformGovernanceService:
    return platform_governance_service
