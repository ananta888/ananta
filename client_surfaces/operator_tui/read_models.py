from __future__ import annotations

from typing import Any


def build_goal_rows(payload: dict[str, Any]) -> list[str]:
    items = payload.get("items") if isinstance(payload, dict) else []
    if not items:
        return ["no goals available"]
    rows: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(f"{item.get('id', '-')} [{item.get('status', 'unknown')}] {item.get('title', item.get('summary', ''))}")
    return rows or ["no goals available"]


def build_task_rows(payload: dict[str, Any]) -> list[str]:
    items = payload.get("items") if isinstance(payload, dict) else []
    if not items:
        return ["no tasks available"]
    rows: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(
            f"{item.get('id', '-')} [{item.get('status', 'unknown')}] "
            f"agent={item.get('agent', '-')} {item.get('title', item.get('summary', ''))}"
        )
    return rows or ["no tasks available"]


def build_inspection_detail(section_id: str, payload: dict[str, Any], selected_index: int) -> list[str]:
    if section_id == "goals":
        rows = build_goal_rows(payload)
    elif section_id == "tasks":
        rows = build_task_rows(payload)
    elif section_id == "templates":
        return build_templates_inspect(payload, selected_index)
    else:
        rows = [f"{key}={value}" for key, value in sorted(payload.items())]
    index = max(0, min(int(selected_index), max(0, len(rows) - 1)))
    return [f"inspect_index={index}", rows[index] if rows else "empty"]


def build_templates_inspect(payload: dict[str, Any], selected_index: int) -> list[str]:
    items: list[dict] = payload.get("items") or []
    idx = max(0, min(int(selected_index), max(0, len(items) - 1)))
    if not items:
        return ["keine Einträge"]
    item = items[idx]
    kind = str(item.get("kind") or "")
    lines: list[str] = []

    if kind == "blueprint":
        raw_id = str(item.get("raw_id") or "")
        raw_list: list[dict] = payload.get("blueprints_raw") or []
        raw = next((b for b in raw_list if str(b.get("id") or "") == raw_id), {})
        lines.append(f"Blueprint: {item.get('title','')}")
        if item.get("description"):
            lines.append(f"  {item['description']}")
        lines.append(f"  seed: {'ja' if item.get('is_seed') else 'nein'}")
        if item.get("base_team_type"):
            lines.append(f"  Basis-Team-Typ: {item['base_team_type']}")
        roles: list[dict] = raw.get("roles") or []
        if roles:
            lines.append("")
            lines.append(f"  Rollen ({len(roles)}):")
            for r in roles:
                req = " *" if r.get("is_required", True) else ""
                tpl = f"  → tpl:{r.get('template_id','')}" if r.get("template_id") else ""
                lines.append(f"    {r.get('name','?')}{req}{tpl}")
        artifacts: list[dict] = raw.get("artifacts") or []
        if artifacts:
            lines.append("")
            lines.append(f"  Artefakte ({len(artifacts)}):")
            for a in artifacts[:6]:
                lines.append(f"    [{a.get('kind','?')}] {a.get('title','?')}")

    elif kind == "template":
        raw_id = str(item.get("raw_id") or "")
        raw_list = payload.get("templates_raw") or []
        raw = next((t for t in raw_list if str(t.get("id") or "") == raw_id), {})
        lines.append(f"Template: {item.get('title','')}")
        if item.get("description"):
            lines.append(f"  {item['description']}")
        lines.append("")
        prompt = str(raw.get("prompt_template") or item.get("prompt_preview") or "")
        if prompt:
            lines.append("  Prompt (Vorschau):")
            for ln in prompt[:400].splitlines()[:8]:
                lines.append(f"    {ln[:60]}")
            if len(prompt) > 400:
                lines.append("    …")

    elif kind == "system_prompt":
        raw_id = str(item.get("raw_id") or "")
        raw_list = payload.get("templates_raw") or []
        raw = next((t for t in raw_list if str(t.get("id") or "") == raw_id), {})
        lines.append(f"System-Prompt: {item.get('title','')}")
        if item.get("description"):
            for desc_line in str(item["description"]).splitlines():
                lines.append(f"  {desc_line}")
        svc = str(item.get("service") or raw.get("service") or "")
        if svc:
            lines.append(f"  Service: {svc}")
        prompt = str(raw.get("prompt_template") or item.get("prompt_preview") or "")
        if prompt:
            lines.append("")
            lines.append("  Prompt-Template (Vorschau):")
            for ln in prompt[:500].splitlines()[:10]:
                lines.append(f"    {ln[:62]}")
            if len(prompt) > 500:
                lines.append("    …")
        lines.append("")
        lines.append("  Hinweis: Datei editieren:")
        lines.append("    config/system_prompts.json")

    return lines or ["kein Detail verfügbar"]
