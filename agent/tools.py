import json
import logging
import re
import typing
from typing import Any, Callable, Dict, List, Optional

from flask import current_app

from agent.db_models import ConfigDB, RoleDB, TeamDB, TeamMemberDB, TeamTypeDB, TeamTypeRoleLink, TemplateDB
from agent.repository import (
    agent_repo,
    audit_repo,
    config_repo,
    role_repo,
    team_member_repo,
    team_repo,
    team_type_repo,
    team_type_role_link_repo,
    template_repo,
)

logger = logging.getLogger(__name__)


class ToolResult:
    def __init__(self, success: bool, output: Any, error: Optional[str] = None):
        self.success = success
        self.output = output
        self.error = error

    def to_dict(self):
        return {"success": self.success, "output": self.output, "error": self.error}


class ToolRegistry:
    def __init__(self):
        self.tools: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, description: str, parameters: Dict[str, Any]):
        def decorator(func: Callable):
            self.tools[name] = {"func": func, "description": description, "parameters": parameters}
            return func

        return decorator

    def get_tool_definitions(
        self, allowlist: Optional[typing.Iterable[str]] = None, denylist: Optional[typing.Iterable[str]] = None
    ) -> List[Dict[str, Any]]:
        defs = []
        allow_all = allowlist is not None and "*" in allowlist
        for name, info in self.tools.items():
            if denylist and name in denylist:
                continue
            if allowlist is not None and not allow_all and name not in allowlist:
                continue
            defs.append({"name": name, "description": info["description"], "parameters": info["parameters"]})
        return defs

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        if name not in self.tools:
            return ToolResult(False, None, f"Tool '{name}' nicht gefunden.")

        try:
            result = self.tools[name]["func"](**args)
            return ToolResult(True, result)
        except Exception as e:
            logger.error(f"Fehler bei Ausführung von Tool '{name}': {e}")
            return ToolResult(False, None, str(e))


registry = ToolRegistry()

TEMPLATE_VAR_PATTERN = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")
DEFAULT_TEMPLATE_VARS = {
    "agent_name",
    "task_title",
    "task_description",
    "team_name",
    "role_name",
    "team_goal",
    "anforderungen",
    "funktion",
    "feature_name",
    "title",
    "description",
    "task",
    "endpoint_name",
    "beschreibung",
    "sprache",
    "api_details",
}


def _get_template_allowlist() -> set:
    cfg = current_app.config.get("AGENT_CONFIG", {})
    allowlist_cfg = cfg.get("template_variables_allowlist")
    if isinstance(allowlist_cfg, list) and allowlist_cfg:
        return set(allowlist_cfg)
    return DEFAULT_TEMPLATE_VARS


def _unknown_template_vars(template_text: str) -> list[str]:
    if not template_text:
        return []
    found_vars = TEMPLATE_VAR_PATTERN.findall(template_text)
    allowlist = _get_template_allowlist()
    return [v for v in found_vars if v not in allowlist]


@registry.register(
    name="create_template",
    description="Creates a new prompt template.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Template name"},
            "description": {"type": "string", "description": "Optional description"},
            "prompt_template": {"type": "string", "description": "Prompt template text"},
        },
        "required": ["name", "prompt_template"],
    },
)
def create_template_tool(name: str, description: str = "", prompt_template: str = ""):
    unknown = _unknown_template_vars(prompt_template)
    tpl = TemplateDB(name=name, description=description, prompt_template=prompt_template)
    template_repo.save(tpl)
    res = tpl.model_dump()
    if unknown:
        res["warnings"] = [
            {
                "type": "unknown_variables",
                "details": f"Unknown variables: {', '.join(unknown)}",
                "allowed": list(_get_template_allowlist()),
            }
        ]
    return res


@registry.register(
    name="update_template",
    description="Updates an existing prompt template.",
    parameters={
        "type": "object",
        "properties": {
            "template_id": {"type": "string", "description": "Template ID"},
            "name": {"type": "string", "description": "Template name"},
            "description": {"type": "string", "description": "Optional description"},
            "prompt_template": {"type": "string", "description": "Prompt template text"},
        },
        "required": ["template_id"],
    },
)
def update_template_tool(
    template_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    prompt_template: Optional[str] = None,
):
    tpl = template_repo.get_by_id(template_id)
    if not tpl:
        return {"error": "not_found"}
    warnings = []
    if prompt_template is not None:
        unknown = _unknown_template_vars(prompt_template)
        if unknown:
            warnings.append(
                {
                    "type": "unknown_variables",
                    "details": f"Unknown variables: {', '.join(unknown)}",
                    "allowed": list(_get_template_allowlist()),
                }
            )
        tpl.prompt_template = prompt_template
    if name is not None:
        tpl.name = name
    if description is not None:
        tpl.description = description
    template_repo.save(tpl)
    res = tpl.model_dump()
    if warnings:
        res["warnings"] = warnings
    return res


@registry.register(
    name="delete_template",
    description="Deletes a prompt template.",
    parameters={
        "type": "object",
        "properties": {"template_id": {"type": "string", "description": "Template ID"}},
        "required": ["template_id"],
    },
)
def delete_template_tool(template_id: str):
    if template_repo.delete(template_id):
        return {"status": "deleted"}
    return {"error": "not_found"}


@registry.register(
    name="create_team",
    description="Erstellt ein neues Team mit einem bestimmten Typ.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name des Teams"},
            "team_type": {"type": "string", "description": "Typ des Teams (z.B. Scrum, Kanban)"},
            "description": {"type": "string", "description": "Optionale Beschreibung"},
        },
        "required": ["name", "team_type"],
    },
)
def create_team_tool(name: str, team_type: str, description: str = ""):
    from agent.routes.teams import ensure_default_templates, initialize_scrum_artifacts, normalize_team_type_name

    normalized_type = normalize_team_type_name(team_type)
    if normalized_type:
        ensure_default_templates(normalized_type)
    tt = team_type_repo.get_by_name(normalized_type or team_type)
    if not tt:
        # Falls der Typ nicht existiert, versuchen wir ihn anzulegen oder geben Fehler
        return f"Team-Typ '{team_type}' wurde nicht gefunden."

    new_team = TeamDB(name=name, description=description, team_type_id=tt.id, is_active=True)

    # Andere Teams deaktivieren (wie in der Heuristik)
    from sqlmodel import Session, select

    from agent.database import engine

    with Session(engine) as session:
        others = session.exec(select(TeamDB)).all()
        for other in others:
            other.is_active = False
            session.add(other)
        session.commit()

    team_repo.save(new_team)

    if (normalized_type or team_type).lower() == "scrum":
        initialize_scrum_artifacts(new_team.name)
        return f"Scrum-Team '{name}' erfolgreich angelegt mit initialen Artefakten."

    return f"Team '{name}' vom Typ '{normalized_type or team_type}' erfolgreich angelegt."


@registry.register(
    name="ensure_team_templates",
    description="Stellt sicher, dass Standard-Templates und Rollen fuer Team-Typen existieren.",
    parameters={
        "type": "object",
        "properties": {
            "team_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Liste von Team-Typen (z.B. Scrum, Kanban).",
            }
        },
    },
)
def ensure_team_templates_tool(team_types: Optional[List[str]] = None):
    from agent.routes.teams import ensure_default_templates

    if not team_types:
        team_types = ["Scrum", "Kanban"]
    results = []
    for team_type in team_types:
        ensure_default_templates(team_type)
        results.append({"team_type": team_type, "status": "ensured"})
    return results


@registry.register(
    name="list_teams",
    description="Listet alle existierenden Teams auf.",
    parameters={"type": "object", "properties": {}},
)
def list_teams_tool():
    teams = team_repo.get_all()
    return [t.model_dump() for t in teams]


@registry.register(
    name="update_config",
    description="Aktualisiert die globale Konfiguration.",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Konfigurationsschlüssel"},
            "value": {"type": "string", "description": "Neuer Wert (als JSON-String oder einfacher Wert)"},
        },
        "required": ["key", "value"],
    },
)
def update_config_tool(key: str, value: Any):
    try:
        val_json = json.dumps(value)
        config_repo.save(ConfigDB(key=key, value_json=val_json))

        # Runtime Update
        try:
            from flask import current_app

            if current_app:
                cfg = current_app.config.get("AGENT_CONFIG", {})
                cfg[key] = value
                current_app.config["AGENT_CONFIG"] = cfg

                # Einstellungen synchronisieren
                from agent.config import settings

                if hasattr(settings, key):
                    try:
                        setattr(settings, key, value)
                    except (AttributeError, ValueError, TypeError):
                        pass
        except Exception as e:
            logger.warning(f"Runtime-Config Update fehlgeschlagen: {e}")

        return f"Konfiguration '{key}' wurde auf '{val_json}' aktualisiert und zur Laufzeit angewendet."
    except Exception as e:
        return f"Fehler beim Aktualisieren der Konfiguration: {e}"


@registry.register(
    name="analyze_logs",
    description="Gibt die letzten Audit-Logs zur Analyse zurück.",
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "description": "Anzahl der Log-Einträge", "default": 20}},
    },
)
def analyze_logs_tool(limit: int = 20):
    logs = audit_repo.get_all(limit=limit)
    return [log_entry.model_dump() for log_entry in logs]


@registry.register(
    name="read_agent_logs",
    description="Liest die letzten Zeilen aus einer spezifischen Log-Datei.",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Name der Log-Datei (z.B. agent.log)"},
            "lines": {"type": "integer", "description": "Anzahl der Zeilen", "default": 50},
        },
        "required": ["filename"],
    },
)
def read_agent_logs_tool(filename: str, lines: int = 50):
    import os

    # Pfad validieren (einfacher Schutz gegen Path Traversal)
    safe_filename = os.path.basename(filename)
    log_path = os.path.join(".", safe_filename)

    if not os.path.exists(log_path):
        return f"Log-Datei '{safe_filename}' nicht gefunden."

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.readlines()
            return "".join(content[-lines:])
    except Exception as e:
        return f"Fehler beim Lesen der Logs: {e}"


@registry.register(
    name="assign_role",
    description="Weist einem Agenten in einem Team eine Rolle zu.",
    parameters={
        "type": "object",
        "properties": {
            "team_id": {"type": "string", "description": "ID des Teams"},
            "agent_url": {"type": "string", "description": "URL des Agenten"},
            "role_id": {"type": "string", "description": "ID der Rolle"},
        },
        "required": ["team_id", "agent_url", "role_id"],
    },
)
def assign_role_tool(team_id: str, agent_url: str, role_id: str):
    from agent.db_models import TeamMemberDB
    from agent.repository import team_member_repo

    # Prüfen ob Mitglied schon existiert
    members = team_member_repo.get_by_team(team_id)
    existing = next((m for m in members if m.agent_url == agent_url), None)

    if existing:
        existing.role_id = role_id
        team_member_repo.save(existing)
        return f"Rolle für Agent '{agent_url}' in Team '{team_id}' auf '{role_id}' aktualisiert."
    else:
        new_member = TeamMemberDB(team_id=team_id, agent_url=agent_url, role_id=role_id)
        team_member_repo.save(new_member)
        return f"Agent '{agent_url}' mit Rolle '{role_id}' zum Team '{team_id}' hinzugefügt."


@registry.register(
    name="list_roles",
    description="Listet alle verfügbaren Rollen auf.",
    parameters={"type": "object", "properties": {}},
)
def list_roles_tool():
    roles = role_repo.get_all()
    return [r.model_dump() for r in roles]


@registry.register(
    name="list_agents",
    description="Listet alle registrierten Agenten auf.",
    parameters={"type": "object", "properties": {}},
)
def list_agents_tool():
    agents = agent_repo.get_all()
    return [a.model_dump() for a in agents]


@registry.register(
    name="list_templates",
    description="Listet alle verfügbaren Prompt-Templates auf.",
    parameters={"type": "object", "properties": {}},
)
def list_templates_tool():
    tpls = template_repo.get_all()
    return [t.model_dump() for t in tpls]


@registry.register(
    name="upsert_team_type",
    description="Creates or updates a team type.",
    parameters={
        "type": "object",
        "properties": {
            "type_id": {"type": "string", "description": "Optional team type ID for update"},
            "name": {"type": "string", "description": "Team type name"},
            "description": {"type": "string", "description": "Optional team type description"},
        },
        "required": ["name"],
    },
)
def upsert_team_type_tool(type_id: Optional[str] = None, name: str = "", description: str = ""):
    from agent.routes.teams import ensure_default_templates, normalize_team_type_name

    normalized_name = normalize_team_type_name(name)
    if not normalized_name:
        return {"error": "name_required"}
    team_type = team_type_repo.get_by_id(type_id) if type_id else team_type_repo.get_by_name(normalized_name)
    if team_type:
        team_type.name = normalized_name
        team_type.description = description or team_type.description
        team_type_repo.save(team_type)
        ensure_default_templates(team_type.name)
        return {"status": "updated", "team_type": team_type.model_dump()}
    created = TeamTypeDB(name=normalized_name, description=description)
    team_type_repo.save(created)
    ensure_default_templates(created.name)
    return {"status": "created", "team_type": created.model_dump()}


@registry.register(
    name="delete_team_type",
    description="Deletes a team type.",
    parameters={
        "type": "object",
        "properties": {"type_id": {"type": "string", "description": "Team type ID"}},
        "required": ["type_id"],
    },
)
def delete_team_type_tool(type_id: str):
    if team_type_repo.delete(type_id):
        return {"status": "deleted", "type_id": type_id}
    return {"error": "not_found"}


@registry.register(
    name="upsert_role",
    description="Creates or updates a role.",
    parameters={
        "type": "object",
        "properties": {
            "role_id": {"type": "string", "description": "Optional role ID for update"},
            "name": {"type": "string", "description": "Role name"},
            "description": {"type": "string", "description": "Optional role description"},
            "default_template_id": {"type": "string", "description": "Optional default template ID"},
        },
        "required": ["name"],
    },
)
def upsert_role_tool(
    role_id: Optional[str] = None,
    name: str = "",
    description: str = "",
    default_template_id: Optional[str] = None,
):
    if not name.strip():
        return {"error": "name_required"}
    if default_template_id and not template_repo.get_by_id(default_template_id):
        return {"error": "template_not_found", "template_id": default_template_id}
    role = role_repo.get_by_id(role_id) if role_id else role_repo.get_by_name(name)
    if role:
        role.name = name
        role.description = description or role.description
        if default_template_id is not None:
            role.default_template_id = default_template_id
        role_repo.save(role)
        return {"status": "updated", "role": role.model_dump()}
    created = RoleDB(name=name, description=description, default_template_id=default_template_id)
    role_repo.save(created)
    return {"status": "created", "role": created.model_dump()}


@registry.register(
    name="delete_role",
    description="Deletes a role.",
    parameters={
        "type": "object",
        "properties": {"role_id": {"type": "string", "description": "Role ID"}},
        "required": ["role_id"],
    },
)
def delete_role_tool(role_id: str):
    if role_repo.delete(role_id):
        return {"status": "deleted", "role_id": role_id}
    return {"error": "not_found"}


@registry.register(
    name="link_role_to_team_type",
    description="Links a role to a team type with an optional template mapping.",
    parameters={
        "type": "object",
        "properties": {
            "type_id": {"type": "string", "description": "Team type ID"},
            "role_id": {"type": "string", "description": "Role ID"},
            "template_id": {"type": "string", "description": "Optional template ID"},
        },
        "required": ["type_id", "role_id"],
    },
)
def link_role_to_team_type_tool(type_id: str, role_id: str, template_id: Optional[str] = None):
    if not team_type_repo.get_by_id(type_id):
        return {"error": "team_type_not_found"}
    if not role_repo.get_by_id(role_id):
        return {"error": "role_not_found"}
    if template_id and not template_repo.get_by_id(template_id):
        return {"error": "template_not_found"}
    existing = team_type_role_link_repo.get_by_team_type(type_id)
    if any(link.role_id == role_id for link in existing):
        return {"status": "already_linked", "type_id": type_id, "role_id": role_id}
    team_type_role_link_repo.save(TeamTypeRoleLink(team_type_id=type_id, role_id=role_id, template_id=template_id))
    return {"status": "linked", "type_id": type_id, "role_id": role_id, "template_id": template_id}


@registry.register(
    name="unlink_role_from_team_type",
    description="Unlinks a role from a team type.",
    parameters={
        "type": "object",
        "properties": {
            "type_id": {"type": "string", "description": "Team type ID"},
            "role_id": {"type": "string", "description": "Role ID"},
        },
        "required": ["type_id", "role_id"],
    },
)
def unlink_role_from_team_type_tool(type_id: str, role_id: str):
    if team_type_role_link_repo.delete(type_id, role_id):
        return {"status": "unlinked", "type_id": type_id, "role_id": role_id}
    return {"error": "not_found"}


@registry.register(
    name="set_role_template_mapping",
    description="Sets or clears template mapping for a team type role link.",
    parameters={
        "type": "object",
        "properties": {
            "type_id": {"type": "string", "description": "Team type ID"},
            "role_id": {"type": "string", "description": "Role ID"},
            "template_id": {"type": "string", "description": "Template ID or null to clear"},
        },
        "required": ["type_id", "role_id"],
    },
)
def set_role_template_mapping_tool(type_id: str, role_id: str, template_id: Optional[str] = None):
    from sqlmodel import Session, select

    from agent.database import engine

    if template_id and not template_repo.get_by_id(template_id):
        return {"error": "template_not_found"}
    with Session(engine) as session:
        link = session.exec(
            select(TeamTypeRoleLink).where(TeamTypeRoleLink.team_type_id == type_id, TeamTypeRoleLink.role_id == role_id)
        ).first()
        if not link:
            return {"error": "not_found"}
        link.template_id = template_id
        session.add(link)
        session.commit()
    return {"status": "updated", "type_id": type_id, "role_id": role_id, "template_id": template_id}


@registry.register(
    name="upsert_team",
    description="Creates or updates a team and optionally its members.",
    parameters={
        "type": "object",
        "properties": {
            "team_id": {"type": "string", "description": "Optional team ID for update"},
            "name": {"type": "string", "description": "Team name"},
            "description": {"type": "string", "description": "Optional team description"},
            "team_type_id": {"type": "string", "description": "Optional team type ID"},
            "is_active": {"type": "boolean", "description": "Optional active flag"},
            "members": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent_url": {"type": "string"},
                        "role_id": {"type": "string"},
                        "custom_template_id": {"type": "string"},
                    },
                    "required": ["agent_url", "role_id"],
                },
            },
        },
        "required": ["name"],
    },
)
def upsert_team_tool(
    name: str,
    team_id: Optional[str] = None,
    description: str = "",
    team_type_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    members: Optional[list[dict[str, Any]]] = None,
):
    if team_type_id and not team_type_repo.get_by_id(team_type_id):
        return {"error": "team_type_not_found"}
    team = team_repo.get_by_id(team_id) if team_id else None
    if team:
        team.name = name
        team.description = description
        if team_type_id is not None:
            team.team_type_id = team_type_id
        if is_active is not None:
            team.is_active = bool(is_active)
        team_repo.save(team)
        op = "updated"
    else:
        team = TeamDB(name=name, description=description, team_type_id=team_type_id, is_active=bool(is_active))
        team_repo.save(team)
        op = "created"
    if members is not None:
        team_member_repo.delete_by_team(team.id)
        for item in members:
            role_id = str(item.get("role_id") or "").strip()
            agent_url = str(item.get("agent_url") or "").strip()
            custom_template_id = item.get("custom_template_id")
            if not role_id or not agent_url:
                continue
            if not role_repo.get_by_id(role_id):
                return {"error": "role_not_found", "role_id": role_id}
            if custom_template_id and not template_repo.get_by_id(custom_template_id):
                return {"error": "template_not_found", "template_id": custom_template_id}
            team_member_repo.save(
                TeamMemberDB(
                    team_id=team.id,
                    agent_url=agent_url,
                    role_id=role_id,
                    custom_template_id=custom_template_id,
                )
            )
    return {"status": op, "team": team.model_dump(), "members_count": len(team_member_repo.get_by_team(team.id))}


@registry.register(
    name="delete_team",
    description="Deletes a team.",
    parameters={
        "type": "object",
        "properties": {"team_id": {"type": "string", "description": "Team ID"}},
        "required": ["team_id"],
    },
)
def delete_team_tool(team_id: str):
    if team_repo.delete(team_id):
        return {"status": "deleted", "team_id": team_id}
    return {"error": "not_found"}


@registry.register(
    name="activate_team",
    description="Activates a team and deactivates all others.",
    parameters={
        "type": "object",
        "properties": {"team_id": {"type": "string", "description": "Team ID"}},
        "required": ["team_id"],
    },
)
def activate_team_tool(team_id: str):
    from sqlmodel import Session, select

    from agent.database import engine

    with Session(engine) as session:
        team = session.get(TeamDB, team_id)
        if not team:
            return {"error": "not_found"}
        others = session.exec(select(TeamDB).where(TeamDB.id != team_id)).all()
        for other in others:
            other.is_active = False
            session.add(other)
        team.is_active = True
        session.add(team)
        session.commit()
    return {"status": "activated", "team_id": team_id}


@registry.register(
    name="configure_auto_planner",
    description="Configures auto-planner settings.",
    parameters={
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean"},
            "auto_followup_enabled": {"type": "boolean"},
            "max_subtasks_per_goal": {"type": "integer"},
            "default_priority": {"type": "string"},
            "auto_start_autopilot": {"type": "boolean"},
            "llm_timeout": {"type": "integer"},
            "llm_retry_attempts": {"type": "integer"},
            "llm_retry_backoff": {"type": "number"},
        },
    },
)
def configure_auto_planner_tool(
    enabled: Optional[bool] = None,
    auto_followup_enabled: Optional[bool] = None,
    max_subtasks_per_goal: Optional[int] = None,
    default_priority: Optional[str] = None,
    auto_start_autopilot: Optional[bool] = None,
    llm_timeout: Optional[int] = None,
    llm_retry_attempts: Optional[int] = None,
    llm_retry_backoff: Optional[float] = None,
):
    from agent.routes.tasks.auto_planner import auto_planner

    cfg = auto_planner.configure(
        enabled=enabled,
        auto_followup_enabled=auto_followup_enabled,
        max_subtasks_per_goal=max_subtasks_per_goal,
        default_priority=default_priority,
        auto_start_autopilot=auto_start_autopilot,
        llm_timeout=llm_timeout,
        llm_retry_attempts=llm_retry_attempts,
        llm_retry_backoff=llm_retry_backoff,
    )
    return {"status": "updated", "auto_planner": cfg}


@registry.register(
    name="configure_triggers",
    description="Configures trigger engine settings.",
    parameters={
        "type": "object",
        "properties": {
            "enabled_sources": {"type": "array", "items": {"type": "string"}},
            "webhook_secrets": {"type": "object"},
            "auto_start_planner": {"type": "boolean"},
            "ip_whitelists": {"type": "object"},
            "rate_limits": {"type": "object"},
        },
    },
)
def configure_triggers_tool(
    enabled_sources: Optional[list[str]] = None,
    webhook_secrets: Optional[dict[str, str]] = None,
    auto_start_planner: Optional[bool] = None,
    ip_whitelists: Optional[dict[str, list[str]]] = None,
    rate_limits: Optional[dict[str, dict[str, int]]] = None,
):
    from agent.routes.tasks.triggers import TRIGGERS_CONFIG_KEY, trigger_engine

    cfg = trigger_engine.configure(
        enabled_sources=enabled_sources,
        webhook_secrets=webhook_secrets,
        auto_start_planner=auto_start_planner,
        ip_whitelists=ip_whitelists,
        rate_limits=rate_limits,
    )
    config_repo.save(ConfigDB(key=TRIGGERS_CONFIG_KEY, value_json=json.dumps(cfg)))
    return {"status": "updated", "triggers": cfg}


@registry.register(
    name="set_autopilot_state",
    description="Starts, stops or ticks autopilot.",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "start|stop|tick"},
            "interval_seconds": {"type": "integer"},
            "max_concurrency": {"type": "integer"},
            "goal": {"type": "string"},
            "team_id": {"type": "string"},
            "budget_label": {"type": "string"},
            "security_level": {"type": "string"},
        },
        "required": ["action"],
    },
)
def set_autopilot_state_tool(
    action: str,
    interval_seconds: Optional[int] = None,
    max_concurrency: Optional[int] = None,
    goal: Optional[str] = None,
    team_id: Optional[str] = None,
    budget_label: Optional[str] = None,
    security_level: Optional[str] = None,
):
    from agent.routes.tasks.autopilot import autonomous_loop

    normalized_action = str(action or "").strip().lower()
    if normalized_action == "start":
        autonomous_loop.start(
            interval_seconds=interval_seconds,
            max_concurrency=max_concurrency,
            goal=goal,
            team_id=team_id,
            budget_label=budget_label,
            security_level=security_level,
            persist=True,
            background=not bool(current_app.testing),
        )
        return {"status": "started", "autopilot": autonomous_loop.status()}
    if normalized_action == "stop":
        autonomous_loop.stop(persist=True)
        return {"status": "stopped", "autopilot": autonomous_loop.status()}
    if normalized_action == "tick":
        tick = autonomous_loop.tick_once()
        return {"status": "ticked", "result": tick, "autopilot": autonomous_loop.status()}
    return {"error": "invalid_action", "allowed": ["start", "stop", "tick"]}


