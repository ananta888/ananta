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
    "link_role_to_team_type": ToolCapability("link_role_to_team_type", "write", True, True, "Link role to team type."),
    "unlink_role_from_team_type": ToolCapability(
        "unlink_role_from_team_type", "write", True, True, "Unlink role from team type."
    ),
    "set_role_template_mapping": ToolCapability(
        "set_role_template_mapping", "write", True, True, "Set role-template mapping."
    ),
    "upsert_team": ToolCapability("upsert_team", "write", True, True, "Create or update team."),
    "delete_team": ToolCapability("delete_team", "write", True, True, "Delete team."),
    "activate_team": ToolCapability("activate_team", "write", True, True, "Activate team."),
    "configure_auto_planner": ToolCapability("configure_auto_planner", "admin", True, True, "Configure auto-planner."),
    "configure_triggers": ToolCapability("configure_triggers", "admin", True, True, "Configure triggers."),
    "set_autopilot_state": ToolCapability("set_autopilot_state", "admin", True, True, "Start/stop/tick autopilot."),
    "update_config": ToolCapability("update_config", "admin", True, True, "Update global configuration."),
    # worker file/shell tools (canonical names)
    "file_read": ToolCapability("file_read", "read", False, False, "Read a file."),
    "file_list": ToolCapability("file_list", "read", False, False, "List directory contents."),
    "file_write": ToolCapability("file_write", "write", False, True, "Write a file."),
    "file_writer": ToolCapability("file_writer", "write", False, True, "Write a file."),
    "file_patch": ToolCapability("file_patch", "write", False, True, "Patch a file."),
    "shell_execute": ToolCapability("shell_execute", "write", False, True, "Execute a shell command."),
    "git_status": ToolCapability("git_status", "read", False, False, "Show git status."),
    "git_diff": ToolCapability("git_diff", "read", False, False, "Show git diff."),
    "git_log": ToolCapability("git_log", "read", False, False, "Show git log."),
    "git_commit": ToolCapability("git_commit", "write", False, True, "Create a git commit."),
    "web_fetch": ToolCapability("web_fetch", "read", False, False, "Fetch a URL."),
    "web_search": ToolCapability("web_search", "read", False, False, "Search the web."),
    "doc_extract": ToolCapability("doc_extract", "read", False, False, "Extract documentation."),
    # common model-generated aliases (Gemma4/OpenAI naming conventions)
    "read_file": ToolCapability("read_file", "read", False, False, "Read a file."),
    "write_file": ToolCapability("write_file", "write", False, True, "Write a file."),
    "list_files": ToolCapability("list_files", "read", False, False, "List files."),
    "context_reader": ToolCapability("context_reader", "read", False, False, "Read context files."),
    "create_file": ToolCapability("create_file", "write", False, True, "Create a file."),
    "edit_file": ToolCapability("edit_file", "write", False, True, "Edit a file."),
    "patch_file": ToolCapability("patch_file", "write", False, True, "Patch a file."),
    "run_command": ToolCapability("run_command", "write", False, True, "Run a shell command."),
    "execute_command": ToolCapability("execute_command", "write", False, True, "Execute a shell command."),
    "bash": ToolCapability("bash", "write", False, True, "Run a bash command."),
    "search_web": ToolCapability("search_web", "read", False, False, "Search the web."),
    "fetch_url": ToolCapability("fetch_url", "read", False, False, "Fetch a URL."),
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
            requires_admin=_to_bool(override.get("requires_admin"), default=(base.requires_admin if base else True)),
            mutates_state=_to_bool(override.get("mutates_state"), default=(base.mutates_state if base else False)),
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


def describe_capabilities(
    contract: dict[str, ToolCapability], allowed_tools: set[str], is_admin: bool
) -> dict[str, Any]:
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
        raw_name = str(tc.get("name") or tc.get("tool_name") or "").strip()
        name = raw_name.rsplit(".", 1)[-1] if "." in raw_name else raw_name
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
