from typing import Any


def _clean_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    return text[: max(1, int(max_chars))]


def _compact_task_item(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean_text(task.get("id"), max_chars=100),
        "title": _clean_text(task.get("title"), max_chars=200),
        "status": _clean_text(task.get("status") or "todo", max_chars=40),
        "priority": _clean_text(task.get("priority") or "P1", max_chars=20),
    }


def _normalize_connection_profile(profile: dict[str, Any]) -> dict[str, Any]:
    profile_id = _clean_text(profile.get("id") or "default", max_chars=80)
    endpoint = _clean_text(profile.get("endpoint") or "http://localhost:8080", max_chars=240)
    environment = _clean_text(profile.get("environment") or "local", max_chars=40).lower()
    auth_mode = _clean_text(profile.get("auth_mode") or "session_token", max_chars=40).lower()
    role = _clean_text(profile.get("role") or "developer", max_chars=40).lower()
    return {
        "id": profile_id,
        "endpoint": endpoint,
        "environment": environment,
        "auth_mode": auth_mode,
        "role": role,
        "kritis_target": bool(profile.get("kritis_target", False)),
    }
