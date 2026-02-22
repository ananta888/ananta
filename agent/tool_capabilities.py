from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCapability:
    tool: str
    category: str
    requires_admin: bool
    mutates_state: bool
    description: str


DEFAULT_TOOL_CAPABILITIES: dict[str, ToolCapability] = {
    "list_teams": ToolCapability("list_teams", "read", True, False, "List all teams."),
    "list_roles": ToolCapability("list_roles", "read", True, False, "List all roles."),
    "list_agents": ToolCapability("list_agents", "read", True, False, "List all registered agents."),
    "list_templates": ToolCapability("list_templates", "read", True, False, "List all prompt templates."),
    "analyze_logs": ToolCapability("analyze_logs", "read", True, False, "Read latest audit logs."),
    "read_agent_logs": ToolCapability("read_agent_logs", "read", True, False, "Read selected agent log file."),
    "create_team": ToolCapability("create_team", "write", True, True, "Create a new team."),
    "assign_role": ToolCapability("assign_role", "write", True, True, "Assign role to team member."),
    "ensure_team_templates": ToolCapability(
        "ensure_team_templates", "write", True, True, "Ensure default templates/roles for team types."
    ),
    "create_template": ToolCapability("create_template", "write", True, True, "Create new template."),
    "update_template": ToolCapability("update_template", "write", True, True, "Update template."),
    "delete_template": ToolCapability("delete_template", "write", True, True, "Delete template."),
    "upsert_team_type": ToolCapability("upsert_team_type", "write", True, True, "Create or update team type."),
    "delete_team_type": ToolCapability("delete_team_type", "write", True, True, "Delete team type."),
    "upsert_role": ToolCapability("upsert_role", "write", True, True, "Create or update role."),
    "delete_role": ToolCapability("delete_role", "write", True, True, "Delete role."),
    "link_role_to_team_type": ToolCapability(
        "link_role_to_team_type", "write", True, True, "Link role to team type."
    ),
    "unlink_role_from_team_type": ToolCapability(
        "unlink_role_from_team_type", "write", True, True, "Unlink role from team type."
    ),
    "set_role_template_mapping": ToolCapability(
        "set_role_template_mapping", "write", True, True, "Set role-template mapping."
    ),
    "upsert_team": ToolCapability("upsert_team", "write", True, True, "Create or update team."),
    "delete_team": ToolCapability("delete_team", "write", True, True, "Delete team."),
    "activate_team": ToolCapability("activate_team", "write", True, True, "Activate team."),
    "configure_auto_planner": ToolCapability(
        "configure_auto_planner", "admin", True, True, "Configure auto-planner."
    ),
    "configure_triggers": ToolCapability("configure_triggers", "admin", True, True, "Configure triggers."),
    "set_autopilot_state": ToolCapability("set_autopilot_state", "admin", True, True, "Start/stop/tick autopilot."),
    "update_config": ToolCapability("update_config", "admin", True, True, "Update global configuration."),
}


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def build_capability_contract(agent_cfg: dict | None = None) -> dict[str, ToolCapability]:
    cfg = (agent_cfg or {}).get("assistant_tool_capabilities", {}) or {}
    overrides = cfg.get("overrides", {}) or {}
    contract = dict(DEFAULT_TOOL_CAPABILITIES)
    for name, override in overrides.items():
        if not isinstance(override, dict):
            continue
        base = contract.get(name)
        contract[name] = ToolCapability(
            tool=name,
            category=str(override.get("category") or (base.category if base else "unknown")),
            requires_admin=_to_bool(
                override.get("requires_admin"), default=(base.requires_admin if base else True)
            ),
            mutates_state=_to_bool(
                override.get("mutates_state"), default=(base.mutates_state if base else False)
            ),
            description=str(override.get("description") or (base.description if base else "")),
        )
    return contract


def resolve_allowed_tools(agent_cfg: dict | None, is_admin: bool, contract: dict[str, ToolCapability]) -> set[str]:
    cfg = agent_cfg or {}
    raw_allowlist = cfg.get("llm_tool_allowlist")
    denylist = set(cfg.get("llm_tool_denylist", []) or [])

    if raw_allowlist is None:
        candidates = set(contract.keys())
    elif raw_allowlist == "*" or (isinstance(raw_allowlist, list) and "*" in raw_allowlist):
        candidates = set(contract.keys())
    elif isinstance(raw_allowlist, list):
        candidates = {str(item) for item in raw_allowlist}
    else:
        candidates = set()

    effective: set[str] = set()
    for name in candidates:
        cap = contract.get(name)
        if cap is None:
            continue
        if cap.requires_admin and not is_admin:
            continue
        if name in denylist:
            continue
        effective.add(name)
    return effective


def describe_capabilities(contract: dict[str, ToolCapability], allowed_tools: set[str], is_admin: bool) -> dict[str, Any]:
    tools = []
    for name in sorted(contract.keys()):
        cap = contract[name]
        tools.append(
            {
                "name": name,
                "category": cap.category,
                "requires_admin": cap.requires_admin,
                "mutates_state": cap.mutates_state,
                "allowed_now": name in allowed_tools,
                "description": cap.description,
            }
        )
    return {"is_admin": bool(is_admin), "allowed_tools": sorted(allowed_tools), "tools": tools}


def validate_tool_calls_against_contract(
    tool_calls: list[dict] | None,
    allowed_tools: set[str],
    contract: dict[str, ToolCapability],
    is_admin: bool = False,
) -> tuple[list[str], dict[str, str]]:
    blocked: list[str] = []
    reasons: dict[str, str] = {}
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            blocked.append("<invalid>")
            reasons["<invalid>"] = "invalid_tool_call"
            continue
        name = str(tc.get("name") or "").strip()
        if not name:
            blocked.append("<missing>")
            reasons["<missing>"] = "missing_tool_name"
            continue
        if name not in contract:
            blocked.append(name)
            reasons[name] = "unknown_tool"
            continue
        cap = contract[name]
        if cap.mutates_state and not is_admin:
            blocked.append(name)
            reasons[name] = "admin_required_for_mutating_tool"
            continue
        if name not in allowed_tools:
            blocked.append(name)
            reasons[name] = "tool_not_allowed_by_capability_contract"
    return list(dict.fromkeys(blocked)), reasons
