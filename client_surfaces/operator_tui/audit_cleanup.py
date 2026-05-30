from __future__ import annotations

from pathlib import Path
from typing import Any


_CLEANUP_ACTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "overview",
        "title": "Cleanup Uebersicht",
        "summary": "Optionen für Audit/Chat/Alles mit Bestätigung",
        "kind": "cleanup_overview",
        "details": [
            "Jede Loeschung braucht eine zweite Bestaetigung (Enter).",
            "Esc bricht den Vorgang jederzeit ab.",
            "Für Chat-Verlauf werden laufende UI-Chats und gespeicherte Input-History geleert.",
        ],
    },
    {
        "id": "audit_only",
        "title": "Nur Audit loeschen",
        "summary": "Audit-DB-Einträge + audit.log",
        "kind": "cleanup_action",
        "storage_scopes": ["audit"],
        "details": [
            "Loescht Tabelle audit_logs in der Datenbank.",
            "Leert data/audit.log.",
        ],
    },
    {
        "id": "prompt_only",
        "title": "Nur Prompt/LLM loeschen",
        "summary": "prompt_traces + llm_log",
        "kind": "cleanup_action",
        "storage_scopes": ["prompt"],
        "details": [
            "Leert data/prompt_traces.jsonl.",
            "Leert data/llm_log.jsonl.",
        ],
    },
    {
        "id": "telemetry_only",
        "title": "Nur Runtime-Telemetrie loeschen",
        "summary": "stats_history + terminal_log",
        "kind": "cleanup_action",
        "storage_scopes": ["telemetry"],
        "details": [
            "Loescht Tabelle stats_history in der Datenbank.",
            "Leert data/stats_history.json und data/terminal_log.jsonl.",
        ],
    },
    {
        "id": "chat_only",
        "title": "Nur Chat-Verlauf loeschen",
        "summary": "TUI-Chatverlauf + gespeicherte Chat-Input-History",
        "kind": "cleanup_action",
        "storage_scopes": [],
        "clear_runtime_chat": True,
        "clear_persisted_chat_history": True,
        "details": [
            "Leert laufende Nachrichtenkanäle im TUI-Chat.",
            "Leert gespeicherte chat_input_history in user.json.",
        ],
    },
    {
        "id": "all",
        "title": "Alles loeschen",
        "summary": "Audit + Prompt/LLM + Telemetrie + Chat-Verlauf",
        "kind": "cleanup_action",
        "storage_scopes": ["audit", "prompt", "telemetry"],
        "clear_runtime_chat": True,
        "clear_persisted_chat_history": True,
        "details": [
            "Kombiniert alle Löschoptionen.",
            "Nutze diese Option nur bei bewusstem Komplett-Reset.",
        ],
    },
)


def build_audit_cleanup_entries() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any]] = []
    datasets: dict[str, Any] = {}
    for action in _CLEANUP_ACTIONS:
        action_id = str(action.get("id") or "")
        if not action_id:
            continue
        dataset_id = f"audit.cleanup.{action_id}"
        title = str(action.get("title") or action_id)
        summary = str(action.get("summary") or "")
        items.append(
            {
                "id": dataset_id,
                "dataset_id": dataset_id,
                "group": "Data Cleanup",
                "title": title,
                "status": "ok",
                "summary": summary,
                "error": "",
            }
        )
        datasets[dataset_id] = {
            "kind": str(action.get("kind") or "cleanup_action"),
            "cleanup_action_id": action_id,
            "title": title,
            "summary": summary,
            "details": [str(line) for line in list(action.get("details") or []) if str(line).strip()],
            "storage_scopes": [str(scope) for scope in list(action.get("storage_scopes") or []) if str(scope).strip()],
            "clear_runtime_chat": bool(action.get("clear_runtime_chat")),
            "clear_persisted_chat_history": bool(action.get("clear_persisted_chat_history")),
        }
    return items, datasets


def _truncate_file(path: Path) -> int:
    if not path.exists():
        return 0
    removed_bytes = int(path.stat().st_size)
    path.write_text("", encoding="utf-8")
    return removed_bytes


def _data_dir() -> Path:
    from agent.utils import get_data_dir

    return Path(get_data_dir())


def _cleanup_audit_storage() -> dict[str, int]:
    from sqlmodel import Session, delete

    from agent.database import engine
    from agent.db_models import AuditLogDB

    with Session(engine) as session:
        result = session.exec(delete(AuditLogDB))
        session.commit()
        deleted_rows = int(getattr(result, "rowcount", 0) or 0)
    removed_bytes = _truncate_file(_data_dir() / "audit.log")
    return {"audit_db_rows": deleted_rows, "audit_log_bytes": removed_bytes}


def _cleanup_prompt_storage() -> dict[str, int]:
    data_dir = _data_dir()
    return {
        "prompt_traces_bytes": _truncate_file(data_dir / "prompt_traces.jsonl"),
        "llm_log_bytes": _truncate_file(data_dir / "llm_log.jsonl"),
    }


def _cleanup_telemetry_storage() -> dict[str, int]:
    from sqlmodel import Session, delete

    from agent.database import engine
    from agent.db_models import StatsSnapshotDB

    with Session(engine) as session:
        result = session.exec(delete(StatsSnapshotDB))
        session.commit()
        deleted_rows = int(getattr(result, "rowcount", 0) or 0)
    data_dir = _data_dir()
    return {
        "stats_db_rows": deleted_rows,
        "stats_history_bytes": _truncate_file(data_dir / "stats_history.json"),
        "terminal_log_bytes": _truncate_file(data_dir / "terminal_log.jsonl"),
    }


def run_audit_cleanup_action(action_id: str) -> dict[str, Any]:
    normalized = str(action_id or "").strip().lower()
    selected = next((item for item in _CLEANUP_ACTIONS if str(item.get("id") or "") == normalized), None)
    if not isinstance(selected, dict):
        raise ValueError(f"unknown cleanup action: {action_id}")

    scopes = [str(scope) for scope in list(selected.get("storage_scopes") or [])]
    counts: dict[str, int] = {}
    if "audit" in scopes:
        counts.update(_cleanup_audit_storage())
    if "prompt" in scopes:
        counts.update(_cleanup_prompt_storage())
    if "telemetry" in scopes:
        counts.update(_cleanup_telemetry_storage())

    return {
        "action_id": normalized,
        "counts": counts,
        "clear_runtime_chat": bool(selected.get("clear_runtime_chat")),
        "clear_persisted_chat_history": bool(selected.get("clear_persisted_chat_history")),
    }
