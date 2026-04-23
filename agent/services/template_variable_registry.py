from __future__ import annotations

import re
from typing import Any

_TEMPLATE_VARIABLE_PATTERN = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")

_TEMPLATE_VARIABLE_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "agent_name",
        "description": "Display name of the executing agent.",
        "scopes": ["agent", "task", "team", "role", "blueprint"],
        "stability": "stable",
        "value_source": "application runtime",
        "may_be_empty": False,
    },
    {
        "name": "task_title",
        "description": "Task title resolved for current execution.",
        "scopes": ["task"],
        "stability": "stable",
        "value_source": "task.title",
        "may_be_empty": False,
    },
    {
        "name": "task_description",
        "description": "Task description resolved for current execution.",
        "scopes": ["task"],
        "stability": "stable",
        "value_source": "task.description",
        "may_be_empty": False,
    },
    {
        "name": "team_name",
        "description": "Resolved team name for assigned task context.",
        "scopes": ["team", "task", "blueprint"],
        "stability": "stable",
        "value_source": "team.name",
        "may_be_empty": True,
    },
    {
        "name": "role_name",
        "description": "Resolved role name for assigned task context.",
        "scopes": ["role", "task", "blueprint"],
        "stability": "stable",
        "value_source": "role.name",
        "may_be_empty": True,
    },
    {
        "name": "team_goal",
        "description": "Primary execution goal for the task (with safe fallbacks).",
        "scopes": ["team", "task", "blueprint"],
        "stability": "stable",
        "value_source": "goal.goal | fallback chain",
        "may_be_empty": False,
    },
    {
        "name": "goal_context",
        "description": "Extended goal context text when available.",
        "scopes": ["task", "team"],
        "stability": "stable",
        "value_source": "goal.context",
        "may_be_empty": True,
    },
    {
        "name": "acceptance_criteria",
        "description": "Goal acceptance criteria rendered as a bullet list.",
        "scopes": ["task", "team"],
        "stability": "stable",
        "value_source": "goal.acceptance_criteria",
        "may_be_empty": True,
    },
    {
        "name": "anforderungen",
        "description": "Legacy German compatibility alias for requirements/goal context.",
        "scopes": ["legacy", "domain_specific"],
        "stability": "legacy",
        "alias_of": "team_goal",
        "value_source": "legacy compatibility",
        "may_be_empty": True,
    },
    {
        "name": "funktion",
        "description": "Legacy German compatibility alias for feature/function context.",
        "scopes": ["legacy", "domain_specific"],
        "stability": "legacy",
        "alias_of": "task_description",
        "value_source": "legacy compatibility",
        "may_be_empty": True,
    },
    {
        "name": "feature_name",
        "description": "Legacy compatibility alias for feature title naming.",
        "scopes": ["legacy", "domain_specific"],
        "stability": "legacy",
        "alias_of": "task_title",
        "value_source": "legacy compatibility",
        "may_be_empty": True,
    },
    {
        "name": "title",
        "description": "Legacy generic alias for task title.",
        "scopes": ["legacy", "task"],
        "stability": "legacy",
        "alias_of": "task_title",
        "value_source": "legacy compatibility",
        "may_be_empty": True,
    },
    {
        "name": "description",
        "description": "Legacy generic alias for task description.",
        "scopes": ["legacy", "task"],
        "stability": "legacy",
        "alias_of": "task_description",
        "value_source": "legacy compatibility",
        "may_be_empty": True,
    },
    {
        "name": "task",
        "description": "Legacy shorthand alias for task title.",
        "scopes": ["legacy", "task"],
        "stability": "legacy",
        "alias_of": "task_title",
        "value_source": "legacy compatibility",
        "may_be_empty": True,
    },
    {
        "name": "endpoint_name",
        "description": "Domain-specific endpoint placeholder for API-oriented templates.",
        "scopes": ["domain_specific", "artifact"],
        "stability": "legacy",
        "value_source": "custom payload",
        "may_be_empty": True,
    },
    {
        "name": "beschreibung",
        "description": "Legacy German alias for description text.",
        "scopes": ["legacy", "domain_specific"],
        "stability": "legacy",
        "alias_of": "task_description",
        "value_source": "legacy compatibility",
        "may_be_empty": True,
    },
    {
        "name": "sprache",
        "description": "Domain-specific language placeholder for generated artifacts.",
        "scopes": ["domain_specific", "artifact"],
        "stability": "legacy",
        "value_source": "custom payload",
        "may_be_empty": True,
    },
    {
        "name": "api_details",
        "description": "Domain-specific API detail payload for frontend/backend collaboration.",
        "scopes": ["domain_specific", "artifact", "blueprint"],
        "stability": "legacy",
        "value_source": "custom payload",
        "may_be_empty": True,
    },
]


def parse_template_variable_names(template_text: str) -> list[str]:
    if not template_text:
        return []
    return _TEMPLATE_VARIABLE_PATTERN.findall(template_text)


def list_template_variable_registry() -> list[dict[str, Any]]:
    return [dict(item) for item in _TEMPLATE_VARIABLE_REGISTRY]


def resolve_allowed_template_variables(agent_cfg: dict | None = None) -> list[str]:
    cfg = agent_cfg if isinstance(agent_cfg, dict) else {}
    allowlist_cfg = cfg.get("template_variables_allowlist")
    if isinstance(allowlist_cfg, list):
        values: list[str] = []
        seen: set[str] = set()
        for raw in allowlist_cfg:
            name = str(raw or "").strip()
            if not name or name in seen:
                continue
            values.append(name)
            seen.add(name)
        if values:
            return values
    return [entry["name"] for entry in _TEMPLATE_VARIABLE_REGISTRY]


def find_unknown_template_variables(template_text: str, *, agent_cfg: dict | None = None) -> list[str]:
    found = parse_template_variable_names(template_text)
    if not found:
        return []
    allowlist = set(resolve_allowed_template_variables(agent_cfg=agent_cfg))
    return [value for value in found if value not in allowlist]


def build_template_variable_registry_payload(agent_cfg: dict | None = None) -> dict[str, Any]:
    entries = list_template_variable_registry()
    allowed_names = resolve_allowed_template_variables(agent_cfg=agent_cfg)
    by_scope: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}
    for item in entries:
        name = str(item.get("name") or "")
        alias_of = str(item.get("alias_of") or "").strip()
        if alias_of:
            aliases[name] = alias_of
        for scope in item.get("scopes") or []:
            by_scope.setdefault(str(scope), []).append(name)
    for key in by_scope:
        by_scope[key] = sorted(by_scope[key])
    return {
        "version": 1,
        "variables": entries,
        "allowed_names": allowed_names,
        "by_scope": by_scope,
        "aliases": aliases,
    }


def build_template_runtime_contract_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "renderer": {
            "name": "task_scoped_prompt_replace",
            "mode": "direct_placeholder_replacement",
            "expression_support": False,
        },
        "context_fields": [
            {"name": "agent_name", "source": "AGENT_NAME runtime setting", "required": True},
            {"name": "task_title", "source": "task.title", "required": True},
            {"name": "task_description", "source": "task.description", "required": True},
            {"name": "team_goal", "source": "goal.goal with fallback chain", "required": True},
            {"name": "goal_context", "source": "goal.context", "required": False},
            {"name": "acceptance_criteria", "source": "goal.acceptance_criteria list", "required": False},
            {"name": "team_name", "source": "resolved team assignment", "required": False},
            {"name": "role_name", "source": "resolved role assignment", "required": False},
        ],
        "validation": {
            "unknown_variable_mode": "warn_by_default",
            "strict_mode_setting": "template_variable_validation.strict",
            "strict_mode_error": "unknown_template_variables",
        },
        "notes": [
            "Template resolution is hub-owned through task/role/team mappings.",
            "Undefined placeholders remain visible in rendered output if they are not replaced by runtime context.",
            "Legacy and domain-specific aliases remain allowed for compatibility.",
        ],
    }
