import enum
import json
import logging
import typing
from typing import Any, Dict, List, Optional, Callable
from agent.repository import team_repo, team_type_repo, role_repo, config_repo, audit_repo, agent_repo
from agent.db_models import TeamDB, ConfigDB, AuditLogDB
from flask import current_app

logger = logging.getLogger(__name__)

class ToolResult:
    def __init__(self, success: bool, output: Any, error: Optional[str] = None):
        self.success = success
        self.output = output
        self.error = error

    def to_dict(self):
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error
        }

class ToolRegistry:
    def __init__(self):
        self.tools: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, description: str, parameters: Dict[str, Any]):
        def decorator(func: Callable):
            self.tools[name] = {
                "func": func,
                "description": description,
                "parameters": parameters
            }
            return func
        return decorator

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": name,
                "description": info["description"],
                "parameters": info["parameters"]
            }
            for name, info in self.tools.items()
        ]

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

@registry.register(
    name="create_team",
    description="Erstellt ein neues Team mit einem bestimmten Typ.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name des Teams"},
            "team_type": {"type": "string", "description": "Typ des Teams (z.B. Scrum, Kanban)"},
            "description": {"type": "string", "description": "Optionale Beschreibung"}
        },
        "required": ["name", "team_type"]
    }
)
def create_team_tool(name: str, team_type: str, description: str = ""):
    from agent.routes.teams import initialize_scrum_artifacts
    
    tt = team_type_repo.get_by_name(team_type)
    if not tt:
        # Falls der Typ nicht existiert, versuchen wir ihn anzulegen oder geben Fehler
        return f"Team-Typ '{team_type}' wurde nicht gefunden."

    new_team = TeamDB(
        name=name,
        description=description,
        team_type_id=tt.id,
        is_active=True
    )
    
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
    
    if team_type.lower() == "scrum":
        initialize_scrum_artifacts(new_team.name)
        return f"Scrum-Team '{name}' erfolgreich angelegt mit initialen Artefakten."
    
    return f"Team '{name}' vom Typ '{team_type}' erfolgreich angelegt."

@registry.register(
    name="list_teams",
    description="Listet alle existierenden Teams auf.",
    parameters={"type": "object", "properties": {}}
)
def list_teams_tool():
    teams = team_repo.get_all()
    return [t.dict() for t in teams]

@registry.register(
    name="update_config",
    description="Aktualisiert die globale Konfiguration.",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Konfigurationsschlüssel"},
            "value": {"type": "string", "description": "Neuer Wert (als JSON-String oder einfacher Wert)"}
        },
        "required": ["key", "value"]
    }
)
def update_config_tool(key: str, value: Any):
    try:
        val_json = json.dumps(value)
        config_repo.save(ConfigDB(key=key, value_json=val_json))
        return f"Konfiguration '{key}' wurde auf '{val_json}' aktualisiert."
    except Exception as e:
        return f"Fehler beim Aktualisieren der Konfiguration: {e}"

@registry.register(
    name="analyze_logs",
    description="Gibt die letzten Audit-Logs zur Analyse zurück.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Anzahl der Log-Einträge", "default": 20}
        }
    }
)
def analyze_logs_tool(limit: int = 20):
    logs = audit_repo.get_all(limit=limit)
    return [l.dict() for l in logs]

@registry.register(
    name="read_agent_logs",
    description="Liest die letzten Zeilen aus einer spezifischen Log-Datei.",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Name der Log-Datei (z.B. agent.log)"},
            "lines": {"type": "integer", "description": "Anzahl der Zeilen", "default": 50}
        },
        "required": ["filename"]
    }
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
            "role_id": {"type": "string", "description": "ID der Rolle"}
        },
        "required": ["team_id", "agent_url", "role_id"]
    }
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
    parameters={"type": "object", "properties": {}}
)
def list_roles_tool():
    roles = role_repo.get_all()
    return [r.dict() for r in roles]

@registry.register(
    name="list_agents",
    description="Listet alle registrierten Agenten auf.",
    parameters={"type": "object", "properties": {}}
)
def list_agents_tool():
    agents = agent_repo.get_all()
    return [a.dict() for a in agents]
