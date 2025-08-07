from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional


class TaskStore:
    """Simple JSON backed task store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> List[Dict]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save(self, tasks: List[Dict]) -> None:
        self.path.write_text(json.dumps(tasks, indent=2), encoding="utf-8")

    def add_task(self, task: str, agent: Optional[str] = None, template: Optional[str] = None) -> Dict:
        tasks = self._load()
        entry: Dict[str, Optional[str]] = {"task": task}
        if agent:
            entry["agent"] = agent
        if template:
            entry["template"] = template
        tasks.append(entry)
        self._save(tasks)
        return entry

    def next_task(self, agent: Optional[str] = None) -> Optional[Dict]:
        tasks = self._load()
        idx = None
        for i, t in enumerate(tasks):
            if agent is None or t.get("agent") in (None, "", agent):
                idx = i
                break
        if idx is None:
            return None
        task = tasks.pop(idx)
        self._save(tasks)
        return task

    def list_tasks(self, agent: Optional[str] = None) -> List[Dict]:
        tasks = self._load()
        if agent:
            return [t for t in tasks if t.get("agent") == agent]
        return tasks
