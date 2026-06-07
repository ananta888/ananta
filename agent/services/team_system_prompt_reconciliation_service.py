"""System prompt catalog → TemplateDB reconciliation.

SRP: read system prompts from the system prompt catalog and sync them
into TemplateDB rows. Separate from reconcile_seed_templates because
system prompts are global (not blueprint-scoped) and follow slightly
different rules (no is_seed flipping for non-seed). Migrated from
team_blueprint_service.py (WFG-029 split) without behaviour change.
"""
from __future__ import annotations

import time

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import TemplateDB


def reconcile_system_prompts(system_catalog) -> list[dict]:
    """Sync system prompts from system_prompts.json into TemplateDB.

    System prompts (names starting with 'system.') are stored alongside
    role-prompt templates in TemplateDB with is_seed=True. User-created
    templates (is_seed=False) are never overwritten.
    """
    all_prompts = system_catalog.get_all_prompts()
    if not all_prompts:
        return []

    reports: list[dict] = []
    for attempt in range(2):
        try:
            reports = _reconcile_system_prompts_once(all_prompts)
            return reports
        except IntegrityError:
            if attempt >= 1:
                raise
            time.sleep(0.05)
    return reports


def _reconcile_system_prompts_once(all_prompts: list[dict]) -> list[dict]:
    reports: list[dict] = []
    with Session(engine) as session:
        existing: dict[str, TemplateDB] = {
            t.name: t for t in session.exec(select(TemplateDB)).all()
        }
        for entry in all_prompts:
            name = str(entry.get("name") or "")
            description = str(entry.get("description") or "")
            prompt_template = str(entry.get("prompt_template") or "")
            if not name or not prompt_template:
                continue

            existing_tpl = existing.get(name)
            if existing_tpl is None:
                session.add(TemplateDB(
                    name=name, description=description,
                    prompt_template=prompt_template, is_seed=True,
                ))
                reports.append({"name": name, "action": "created"})
                continue

            if not getattr(existing_tpl, "is_seed", True):
                reports.append({"name": name, "action": "skipped_user_template"})
                continue

            changes: list[str] = []
            if existing_tpl.prompt_template != prompt_template:
                existing_tpl.prompt_template = prompt_template
                changes.append("prompt_template")
            if existing_tpl.description != description:
                existing_tpl.description = description
                changes.append("description")
            if changes:
                session.add(existing_tpl)
                reports.append({"name": name, "action": "updated", "fields": changes})
            else:
                reports.append({"name": name, "action": "unchanged"})

        session.commit()
    return reports
