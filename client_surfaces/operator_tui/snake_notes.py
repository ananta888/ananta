"""T05.01 + T05.02 + T05.03: Privater lokaler Notizblock (notes:self).

Speicherpfad: ~/.config/ananta/snake_notes.jsonl
Invariante: Notes werden NIE an Hub, andere Snakes oder AI gesendet.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


_NOTES_FILE = "snake_notes.jsonl"
_MAX_LOAD = 500


def _notes_path() -> Path:
    return Path.home() / ".config" / "ananta" / _NOTES_FILE


def _config_dir() -> Path:
    return Path.home() / ".config" / "ananta"


def load_notes(disabled: bool = False) -> list[dict[str, Any]]:
    """Load last _MAX_LOAD notes from JSONL. Corrupt lines are skipped."""
    if disabled:
        return []
    path = _notes_path()
    if not path.exists():
        return []
    notes: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                notes.append(obj)
        except Exception:
            pass  # skip corrupt lines
    return notes[-_MAX_LOAD:]


def append_note(text: str, *, disabled: bool = False, pinned: bool = False) -> dict[str, Any] | None:
    """Append a single note to JSONL. Returns the note dict or None if disabled."""
    if disabled:
        return None
    note: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "created_at": time.time(),
        "text": str(text)[:500],
        "pinned": pinned,
        "deleted": False,
        "visibility": "local_only",
    }
    path = _notes_path()
    try:
        _config_dir().mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(note) + "\n")
    except Exception:
        pass
    return note


def rewrite_notes(notes: list[dict[str, Any]], *, disabled: bool = False) -> None:
    """Overwrite JSONL with current notes list (used after pin/delete)."""
    if disabled:
        return
    path = _notes_path()
    try:
        _config_dir().mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for note in notes:
                f.write(json.dumps(note) + "\n")
    except Exception:
        pass


def pin_note(notes: list[dict[str, Any]], note_id: str) -> bool:
    for note in notes:
        if note.get("id") == note_id:
            note["pinned"] = True
            return True
    return False


def unpin_note(notes: list[dict[str, Any]], note_id: str) -> bool:
    for note in notes:
        if note.get("id") == note_id:
            note["pinned"] = False
            return True
    return False


def delete_note(notes: list[dict[str, Any]], note_id: str) -> bool:
    """Tombstone a note (sets deleted=True, keeps in list for history)."""
    for note in notes:
        if note.get("id") == note_id:
            note["deleted"] = True
            return True
    return False


def search_notes(notes: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Return non-deleted notes whose text contains query (case-insensitive). Does not modify file."""
    q = query.lower().strip()
    if not q:
        return [n for n in notes if not n.get("deleted")]
    return [n for n in notes if not n.get("deleted") and q in str(n.get("text") or "").lower()]


def visible_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [n for n in notes if not n.get("deleted")]
