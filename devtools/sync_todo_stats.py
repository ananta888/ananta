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

    items = [
        item
        for category in categories
        for item in category.get("items", [])
        if isinstance(item, dict)
    ]
    if items:
        by_status: dict[str, int] = {"completed": 0, "partial": 0, "open": 0}
        for item in items:
            status = str(item.get("status") or "open").strip().lower()
            by_status[status] = by_status.get(status, 0) + 1

        meta = data.setdefault("meta", {})
        meta["total_items"] = len(items)
        meta["by_status"] = by_status
        data.pop("statistics", None)
        return data

    completed_in_session = data.get("completed_in_session") or []
    discovered = data.get("newly_discovered_tasks") or []
    tasks = [
        task
        for category in categories
        for task in category.get("tasks", [])
        if isinstance(task, dict)
    ]
    remaining = sum(
        1
        for task in tasks
        if str(task.get("status") or "").strip().lower() not in {"completed", "cancelled"}
    )

    stats = data.setdefault("statistics", {})
    stats["completed_this_session"] = len(completed_in_session)
    stats["remaining_tasks"] = remaining
    if "newly_discovered_tasks" in data:
        stats["newly_discovered"] = len(discovered)
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
