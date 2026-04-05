from __future__ import annotations

from typing import Any

from agent.services.repository_registry import get_repository_registry


def _read_value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def resolve_task_role_template(task: Any, repos: Any = None) -> dict[str, str | None]:
    repos = repos or get_repository_registry()

    role_id = str(_read_value(task, "assigned_role_id") or "").strip()
    team_id = str(_read_value(task, "team_id") or "").strip()
    assigned_agent_url = str(_read_value(task, "assigned_agent_url") or "").strip()

    role_name = ""
    template_id = ""
    template_name = ""
    team_name = ""

    members = []
    if team_id:
        try:
            members = repos.team_member_repo.get_by_team(team_id) or []
        except Exception:
            members = []

    if team_id and assigned_agent_url:
        for member in members:
            if str(getattr(member, "agent_url", "") or "").strip() != assigned_agent_url:
                continue
            if not role_id:
                role_id = str(getattr(member, "role_id", "") or "").strip()
            template_id = str(getattr(member, "custom_template_id", "") or "").strip()
            break

    team = None
    if team_id:
        try:
            team = repos.team_repo.get_by_id(team_id)
        except Exception:
            team = None
        if team is not None:
            team_name = str(getattr(team, "name", "") or "").strip()

    if role_id and not template_id and team is not None:
        mappings = getattr(team, "role_templates", None)
        if isinstance(mappings, dict):
            template_id = str(mappings.get(role_id) or "").strip()

    if role_id:
        try:
            role = repos.role_repo.get_by_id(role_id)
        except Exception:
            role = None
        if role is not None:
            role_name = str(getattr(role, "name", "") or "").strip()
            if not template_id:
                template_id = str(getattr(role, "default_template_id", "") or "").strip()

    if template_id:
        try:
            template = repos.template_repo.get_by_id(template_id)
        except Exception:
            template = None
        if template is not None:
            template_name = str(getattr(template, "name", "") or "").strip()

    return {
        "role_id": role_id or None,
        "role_name": role_name or None,
        "template_id": template_id or None,
        "template_name": template_name or None,
        "team_id": team_id or None,
        "team_name": team_name or None,
    }
