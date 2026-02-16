import json
import logging
import typing
import re
from typing import Any, Dict, List, Optional, Callable
from agent.repository import team_repo, team_type_repo, role_repo, config_repo, audit_repo, agent_repo, template_repo
from agent.db_models import TeamDB, ConfigDB, TemplateDB
from flask import current_app

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
            logger.error(f"Fehler bei Ausf?hrung von Tool '{name}': {e}")
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
    from agent.routes.teams import initialize_scrum_artifacts, ensure_default_templates, normalize_team_type_name

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
            "key": {"type": "string", "description": "Konfigurationsschl?ssel"},
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
    description="Gibt die letzten Audit-Logs zur Analyse zur?ck.",
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "description": "Anzahl der Log-Eintr?ge", "default": 20}},
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

    # Pr?fen ob Mitglied schon existiert
    members = team_member_repo.get_by_team(team_id)
    existing = next((m for m in members if m.agent_url == agent_url), None)

    if existing:
        existing.role_id = role_id
        team_member_repo.save(existing)
        return f"Rolle f?r Agent '{agent_url}' in Team '{team_id}' auf '{role_id}' aktualisiert."
    else:
        new_member = TeamMemberDB(team_id=team_id, agent_url=agent_url, role_id=role_id)
        team_member_repo.save(new_member)
        return f"Agent '{agent_url}' mit Rolle '{role_id}' zum Team '{team_id}' hinzugef?gt."


@registry.register(
    name="list_roles",
    description="Listet alle verf?gbaren Rollen auf.",
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
    description="Listet alle verf?gbaren Prompt-Templates auf.",
    parameters={"type": "object", "properties": {}},
)
def list_templates_tool():
    tpls = template_repo.get_all()
    return [t.model_dump() for t in tpls]


