"""Sync a tracked todo JSON's status summaries to match the task list.

Usage:
    python scripts/todo_status_sync.py todos/todo.<name>.json

Updates:
- `tasks[].status` for tasks whose `progress_percent == 100` are flipped to `done`
- `tasks[].status` for tasks whose `progress_percent > 0` and < 100 are flipped to `partial`
- `tasks_status_summary` is rebuilt from scratch
- `tasks_type_summary` is rebuilt from scratch
- `progress_summary` is rebuilt from scratch
- `milestones[].status` is derived from contained task statuses (all done = done; any partial = partial; any in_progress = in_progress; else todo)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

STATUSES = ("todo", "in_progress", "partial", "blocked", "done")
PRIORITIES = ("P0", "P1", "P2", "P3")
RISKS = ("low", "medium", "high", "critical")


def _derive_status(progress: int) -> str:
    if progress >= 100:
        return "done"
    if progress > 0:
        return "partial"
    return "todo"


def _milestone_status(task_statuses: list[str]) -> str:
    if not task_statuses:
        return "todo"
    unique = set(task_statuses)
    if unique == {"done"}:
        return "done"
    if "in_progress" in unique:
        return "in_progress"
    if "partial" in unique:
        return "partial"
    if "blocked" in unique:
        return "blocked"
    return "todo"


def _by_priority(tasks: list[dict]) -> dict[str, int]:
    counter: dict[str, int] = {p: 0 for p in PRIORITIES}
    for t in tasks:
        p = t.get("priority", "P3")
        if p in counter:
            counter[p] += 1
    return counter


def _by_risk(tasks: list[dict]) -> dict[str, int]:
    counter: dict[str, int] = {r: 0 for r in RISKS}
    for t in tasks:
        r = t.get("risk", "medium")
        if r in counter:
            counter[r] += 1
    return counter


def _by_type(tasks: list[dict]) -> dict:
    bucket: dict[str, dict] = {}
    for t in tasks:
        kind = t.get("type", "unknown")
        b = bucket.setdefault(kind, {
            "total": 0, "done": 0, "partial": 0, "blocked": 0,
            "todo": 0, "in_progress": 0, "progress_percent": 0,
        })
        b["total"] += 1
        st = t.get("status", "todo")
        if st in b:
            b[st] += 1
        prog = int(t.get("progress_percent", 0) or 0)
        b["progress_percent"] += prog
    for b in bucket.values():
        b["progress_percent"] = round(b["progress_percent"] / max(1, b["total"]), 1)
        b["by_status"] = {
            "todo": b["todo"], "in_progress": b["in_progress"],
            "partial": b["partial"], "blocked": b["blocked"],
            "done": b["done"],
        }
    return bucket


def sync(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks") or []
    if not tasks:
        print(f"  no tasks in {path}", file=sys.stderr)
        return 1

    for t in tasks:
        prog = int(t.get("progress_percent", 0) or 0)
        if t.get("status") not in {"blocked"}:   # respect explicit blocked
            t["status"] = _derive_status(prog)

    by_status: dict[str, int] = {s: 0 for s in STATUSES}
    for t in tasks:
        s = t.get("status", "todo")
        by_status[s] = by_status.get(s, 0) + 1
    total = len(tasks)
    done = by_status["done"]
    progress_percent_done = round(100 * done / total, 1) if total else 0

    data["tasks_status_summary"] = {
        "total": total,
        "by_status": by_status,
        "progress_percent_done": progress_percent_done,
        "by_priority": _by_priority(tasks),
        "by_risk": _by_risk(tasks),
        "critical_path": {
            "total": len(data.get("critical_path_tasks") or []),
            "done": sum(1 for tid in (data.get("critical_path_tasks") or [])
                         if any(t["id"] == tid and t["status"] == "done" for t in tasks)),
            "remaining": 0,
        },
        "milestones": {
            "total": len(data.get("milestones") or []),
            "todo": 0, "in_progress": 0, "partial": 0, "blocked": 0, "done": 0,
        },
    }
    crit_total = data["tasks_status_summary"]["critical_path"]["total"]
    crit_done = data["tasks_status_summary"]["critical_path"]["done"]
    data["tasks_status_summary"]["critical_path"]["remaining"] = crit_total - crit_done

    milestone_statuses: list[str] = []
    for ms in data.get("milestones") or []:
        tids = ms.get("task_ids") or []
        statuses = [t.get("status", "todo") for t in tasks if t.get("id") in tids]
        ms_status = _milestone_status(statuses)
        ms["status"] = ms_status
        milestone_statuses.append(ms_status)
        data["tasks_status_summary"]["milestones"][ms_status] += 1

    data["tasks_type_summary"] = {
        "total": total,
        "by_type": _by_type(tasks),
    }

    data["progress_summary"] = {
        "state": "done" if done == total else ("in_progress" if done else "todo"),
        "todo_remaining": total - done,
        "in_progress": by_status["in_progress"],
        "partial": by_status["partial"],
        "blocked": by_status["blocked"],
        "done": done,
        "milestones_done": milestone_statuses.count("done"),
        "milestones_total": len(milestone_statuses),
    }

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  {path.name}: {done}/{total} done ({progress_percent_done}%), "
          f"milestones {data['progress_summary']['milestones_done']}/"
          f"{data['progress_summary']['milestones_total']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("paths", nargs="+", type=Path)
    args = p.parse_args()
    rc = 0
    for path in args.paths:
        if not path.exists():
            print(f"  missing: {path}", file=sys.stderr)
            rc = 1
            continue
        rc |= sync(path)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
