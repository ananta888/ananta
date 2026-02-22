from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TODO_PATH = ROOT / "todo.json"


def _load_todo() -> dict:
    with TODO_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("todo.json root must be an object")
    return data


def _recompute(data: dict) -> dict:
    categories = data.get("categories") or []
    completed_in_session = data.get("completed_in_session") or []
    discovered = data.get("newly_discovered_tasks") or []

    remaining = 0
    for category in categories:
        for task in category.get("tasks") or []:
            status = str(task.get("status") or "").strip().lower()
            if status not in {"completed", "cancelled"}:
                remaining += 1

    stats = data.get("statistics") or {}
    stats["completed_this_session"] = len(completed_in_session)
    stats["remaining_tasks"] = remaining
    if "newly_discovered_tasks" in data:
        stats["newly_discovered"] = len(discovered)
    data["statistics"] = stats
    return data


def main() -> int:
    data = _load_todo()
    updated = _recompute(data)
    with TODO_PATH.open("w", encoding="utf-8") as fh:
        json.dump(updated, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("todo.json statistics synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
