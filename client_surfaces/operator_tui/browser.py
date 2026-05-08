from __future__ import annotations

from urllib.parse import quote


SECTION_BROWSER_PATHS = {
    "dashboard": "/",
    "goals": "/goals",
    "tasks": "/tasks",
    "artifacts": "/artifacts",
    "knowledge": "/knowledge",
    "config": "/config",
    "system": "/system",
    "audit": "/audit",
    "help": "/help",
}


def browser_fallback_url(base_url: str, section_id: str, target_id: str = "") -> str:
    base = str(base_url or "").rstrip("/")
    path = SECTION_BROWSER_PATHS.get(str(section_id or "").lower(), "/")
    if target_id:
        return f"{base}{path}?target={quote(str(target_id))}"
    return f"{base}{path}"
