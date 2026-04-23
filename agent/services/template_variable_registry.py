from __future__ import annotations

import re
from typing import Any

_TEMPLATE_VARIABLE_PATTERN = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")

SUPPORTED_TEMPLATE_CONTEXT_SCOPES = (
    "task",
    "team",
    "role",
    "blueprint",
    "agent",
    "artifact",
    "domain_specific",
)

_CONTEXT_SCOPE_CAPABILITIES: dict[str, set[str]] = {
    "task": {"task", "team", "role", "agent", "blueprint"},
    "team": {"team", "role", "agent", "blueprint"},
    "role": {"role", "team", "agent"},
    "blueprint": {"blueprint", "team", "role", "agent"},
    "agent": {"agent"},
    "artifact": {"artifact", "domain_specific"},
    "domain_specific": {"domain_specific", "artifact"},
}

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
        "description": "Legacy German alias for requirements and goal context.",
        "scopes": ["legacy", "domain_specific"],
        "stability": "legacy",
        "alias_of": "team_goal",
        "value_source": "legacy compatibility",
        "may_be_empty": True,
    },
    {
        "name": "funktion",
        "description": "Legacy German alias for feature/function context.",
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
        "description": "Domain-specific endpoint placeholder for API templates.",
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
        "description": "Domain-specific API detail payload for collaboration templates.",
        "scopes": ["domain_specific", "artifact", "blueprint"],
        "stability": "legacy",
        "value_source": "custom payload",
        "may_be_empty": True,
    },
]

_SAMPLE_CONTEXTS: dict[str, dict[str, Any]] = {
    "task": {
        "label": "Task runtime",
        "description": "Representative payload for task execution templates.",
        "values": {
            "agent_name": "OpenCode Worker",
            "task_title": "Implement API Validation",
            "task_description": "Introduce strict validation and regression tests.",
            "team_name": "Platform Team",
            "role_name": "Backend Developer",
            "team_goal": "Ship safe template variable validation",
            "goal_context": "Focus on additive behavior and compatibility.",
            "acceptance_criteria": "- validate unknown placeholders\n- preserve strict mode",
        },
    },
    "team": {
        "label": "Team setup",
        "description": "Representative payload for team-level templates.",
        "values": {
            "agent_name": "Team Facilitator",
            "team_name": "Delivery Team",
            "role_name": "Scrum Master",
            "team_goal": "Deliver the next release increment",
        },
    },
    "role": {
        "label": "Role setup",
        "description": "Representative payload for role-centric templates.",
        "values": {
            "agent_name": "Role Owner",
            "role_name": "Security Reviewer",
            "team_name": "Security Team",
            "team_goal": "Review release candidate risk profile",
        },
    },
    "blueprint": {
        "label": "Blueprint authoring",
        "description": "Representative payload for blueprint-level templates.",
        "values": {
            "agent_name": "Blueprint Designer",
            "team_name": "Research Evolution",
            "role_name": "Research Lead",
            "team_goal": "Prepare blueprint work profile for new initiative",
            "api_details": "Blueprint contract v1 with staged artifact outputs",
        },
    },
    "agent": {
        "label": "Agent global",
        "description": "Representative payload for agent-global templates.",
        "values": {
            "agent_name": "Ananta Hub Agent",
        },
    },
    "artifact": {
        "label": "Artifact/domain handoff",
        "description": "Representative payload for artifact-oriented placeholders.",
        "values": {
            "endpoint_name": "POST /templates/preview",
            "sprache": "Python",
            "api_details": "Returns preview text and missing placeholders.",
        },
    },
    "domain_specific": {
        "label": "Domain-specific",
        "description": "Representative payload for legacy domain-specific placeholders.",
        "values": {
            "anforderungen": "Validierung und Vorschau muessen reproduzierbar sein.",
            "funktion": "Template-Vorschau",
            "feature_name": "Template Variable Diagnostics",
            "beschreibung": "Fehlerbilder fuer Operatoren nachvollziehbar machen.",
            "endpoint_name": "POST /templates/validation-diagnostics",
            "sprache": "TypeScript",
            "api_details": "Antwort mit severity, issue codes und safe summary.",
        },
    },
}


def _registry_by_name() -> dict[str, dict[str, Any]]:
    return {str(item.get("name")): dict(item) for item in _TEMPLATE_VARIABLE_REGISTRY}


def normalize_template_context_scope(scope: str | None) -> str | None:
    normalized = str(scope or "").strip().lower()
    if not normalized:
        return None
    if normalized in SUPPORTED_TEMPLATE_CONTEXT_SCOPES:
        return normalized
    return None


def parse_template_variable_names(template_text: str) -> list[str]:
    if not template_text:
        return []
    return _TEMPLATE_VARIABLE_PATTERN.findall(template_text)


def list_template_variable_registry() -> list[dict[str, Any]]:
    return [dict(item) for item in _TEMPLATE_VARIABLE_REGISTRY]


def list_supported_template_context_scopes() -> list[str]:
    return list(SUPPORTED_TEMPLATE_CONTEXT_SCOPES)


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


def _is_variable_available_in_context(
    name: str,
    context_scope: str | None,
    registry_by_name: dict[str, dict[str, Any]] | None = None,
) -> bool:
    if context_scope is None:
        return True
    registry = registry_by_name or _registry_by_name()
    entry = registry.get(name)
    if not entry:
        return False
    scopes = {str(item) for item in (entry.get("scopes") or [])}
    capabilities = _CONTEXT_SCOPE_CAPABILITIES.get(context_scope, {context_scope})
    if scopes.intersection(capabilities):
        return True
    alias_of = str(entry.get("alias_of") or "").strip()
    if alias_of:
        return _is_variable_available_in_context(alias_of, context_scope, registry)
    if "legacy" in scopes and "domain_specific" in scopes:
        return context_scope in {"artifact", "domain_specific"}
    return False


def find_unknown_template_variables(
    template_text: str,
    *,
    agent_cfg: dict | None = None,
) -> list[str]:
    found = parse_template_variable_names(template_text)
    if not found:
        return []
    allowlist = set(resolve_allowed_template_variables(agent_cfg=agent_cfg))
    unknown: list[str] = []
    for value in found:
        if value not in allowlist and value not in unknown:
            unknown.append(value)
    return unknown


def validate_template_variables_with_context(
    template_text: str,
    *,
    context_scope: str | None = None,
    agent_cfg: dict | None = None,
) -> dict[str, Any]:
    registry = _registry_by_name()
    normalized_scope = normalize_template_context_scope(context_scope)
    found_all = parse_template_variable_names(template_text)
    found_unique: list[str] = []
    for name in found_all:
        if name not in found_unique:
            found_unique.append(name)

    allowlist = set(resolve_allowed_template_variables(agent_cfg=agent_cfg))
    unknown: list[str] = []
    known: list[str] = []
    for name in found_unique:
        if name in allowlist:
            known.append(name)
        else:
            unknown.append(name)

    context_invalid: list[str] = []
    deprecated: list[str] = []
    for name in known:
        entry = registry.get(name) or {}
        if str(entry.get("stability") or "").strip().lower() == "legacy":
            deprecated.append(name)
        if normalized_scope and not _is_variable_available_in_context(
            name,
            normalized_scope,
            registry_by_name=registry,
        ):
            context_invalid.append(name)

    duplicate_variables = sorted(
        {
            name
            for name in found_all
            if found_all.count(name) > 1
        }
    )
    issues: list[dict[str, Any]] = []
    if unknown:
        issues.append(
            {
                "code": "unknown_variables",
                "severity": "error",
                "variables": unknown,
                "details": "Variables are not part of the canonical allowlist.",
            }
        )
    if context_invalid:
        issues.append(
            {
                "code": "context_unavailable_variables",
                "severity": "error",
                "variables": context_invalid,
                "details": "Variables are known but unavailable in the selected context.",
            }
        )
    if deprecated:
        issues.append(
            {
                "code": "deprecated_variables",
                "severity": "warning",
                "variables": deprecated,
                "details": "Variables are legacy aliases and should be migrated.",
            }
        )
    if duplicate_variables:
        issues.append(
            {
                "code": "duplicate_variables",
                "severity": "info",
                "variables": duplicate_variables,
                "details": "Variables appear multiple times in the same template.",
            }
        )

    available_for_context = []
    if normalized_scope:
        available_for_context = sorted(
            [
                name
                for name in allowlist
                if _is_variable_available_in_context(
                    name,
                    normalized_scope,
                    registry_by_name=registry,
                )
            ]
        )

    return {
        "context_scope": normalized_scope,
        "supported_context_scopes": list_supported_template_context_scopes(),
        "found_variables": found_unique,
        "unknown_variables": unknown,
        "context_invalid_variables": context_invalid,
        "deprecated_variables": deprecated,
        "duplicate_variables": duplicate_variables,
        "issues": issues,
        "available_variables_for_context": available_for_context,
        "summary": {
            "found_count": len(found_unique),
            "unknown_count": len(unknown),
            "context_invalid_count": len(context_invalid),
            "deprecated_count": len(deprecated),
            "issue_count": len(issues),
        },
    }


def resolve_template_validation_context(
    *,
    request_scope: str | None,
    config_scope: str | None,
) -> str | None:
    return normalize_template_context_scope(request_scope) or normalize_template_context_scope(config_scope)


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
        "version": 2,
        "variables": entries,
        "allowed_names": allowed_names,
        "supported_context_scopes": list_supported_template_context_scopes(),
        "by_scope": by_scope,
        "aliases": aliases,
    }


def build_template_sample_contexts_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "default_context_scope": "task",
        "contexts": {key: dict(value) for key, value in _SAMPLE_CONTEXTS.items()},
    }


def resolve_template_sample_context(
    *,
    context_scope: str | None = None,
    sample_context: str | None = None,
    context_payload: dict | None = None,
) -> tuple[str, dict[str, Any]]:
    normalized_sample_name = normalize_template_context_scope(sample_context)
    normalized_scope = normalize_template_context_scope(context_scope)
    selected = normalized_sample_name or normalized_scope or "task"
    values = dict((_SAMPLE_CONTEXTS.get(selected) or _SAMPLE_CONTEXTS["task"]).get("values") or {})
    if isinstance(context_payload, dict):
        for key, value in context_payload.items():
            normalized_key = str(key or "").strip()
            if not normalized_key:
                continue
            values[normalized_key] = value
    return selected, values


def render_template_preview(
    template_text: str,
    *,
    context_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = context_payload if isinstance(context_payload, dict) else {}
    rendered = str(template_text or "")
    found_variables = parse_template_variable_names(rendered)
    missing_variables: list[str] = []
    for variable in found_variables:
        if variable in payload and payload.get(variable) is not None:
            rendered = rendered.replace(f"{{{{{variable}}}}}", str(payload.get(variable)))
            continue
        if variable not in missing_variables:
            missing_variables.append(variable)
    unresolved_variables = parse_template_variable_names(rendered)
    return {
        "rendered_text": rendered,
        "missing_variables": missing_variables,
        "unresolved_variables": sorted(set(unresolved_variables)),
        "is_complete": len(unresolved_variables) == 0,
    }


def build_template_validation_diagnostics(
    template_text: str,
    *,
    context_scope: str | None = None,
    agent_cfg: dict | None = None,
    preview_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validation = validate_template_variables_with_context(
        template_text,
        context_scope=context_scope,
        agent_cfg=agent_cfg,
    )
    preview = render_template_preview(template_text, context_payload=preview_context)
    severity = "ok"
    if validation["unknown_variables"] or validation["context_invalid_variables"]:
        severity = "error"
    elif validation["deprecated_variables"]:
        severity = "warning"
    return {
        "severity": severity,
        "safe_mode": True,
        "issue_count": validation["summary"]["issue_count"],
        "issues": validation["issues"],
        "rendering": {
            "is_complete": bool(preview["is_complete"]),
            "missing_variables": list(preview["missing_variables"]),
            "unresolved_variables": list(preview["unresolved_variables"]),
        },
        "context_keys": sorted(list((preview_context or {}).keys())),
    }


def build_template_runtime_contract_payload() -> dict[str, Any]:
    return {
        "version": 2,
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
            {
                "name": "acceptance_criteria",
                "source": "goal.acceptance_criteria list",
                "required": False,
            },
            {"name": "team_name", "source": "resolved team assignment", "required": False},
            {"name": "role_name", "source": "resolved role assignment", "required": False},
        ],
        "validation": {
            "unknown_variable_mode": "warn_by_default",
            "strict_mode_setting": "template_variable_validation.strict",
            "strict_context_setting": "template_variable_validation.context_scope",
            "strict_error_codes": [
                "unknown_template_variables",
                "context_unavailable_template_variables",
                "template_validation_failed",
            ],
        },
        "notes": [
            "Template resolution is hub-owned through task/role/team mappings.",
            "Undefined placeholders remain visible in rendered output if they are not replaced.",
            "Legacy aliases remain supported for compatibility and can be migrated gradually.",
        ],
    }
