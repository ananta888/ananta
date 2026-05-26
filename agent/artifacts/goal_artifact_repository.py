from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.config import settings

from .goal_artifact_graph import build_empty_goal_artifact_graph, validate_goal_artifact_graph_payload


class GoalArtifactRepository:
    def __init__(self, *, root: Path | None = None) -> None:
        base = Path(root or settings.data_dir).expanduser().resolve()
        self._root = base / "artifacts" / "goals"
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for_goal(self, goal_id: str) -> Path:
        normalized = str(goal_id or "").strip()
        if not normalized:
            raise ValueError("goal_id_required")
        return self._root / f"{normalized}.json"

    def get_graph(self, goal_id: str) -> dict[str, Any]:
        path = self._path_for_goal(goal_id)
        if not path.exists():
            graph = build_empty_goal_artifact_graph(goal_id=goal_id)
            self.save_graph(graph)
            return graph
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("goal_artifact_graph_invalid")
        errors = validate_goal_artifact_graph_payload(payload)
        if errors:
            raise ValueError(f"goal_artifact_graph_invalid:{'; '.join(errors)}")
        return payload

    def save_graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        goal_id = str(graph.get("goal_id") or "").strip()
        if not goal_id:
            raise ValueError("goal_id_required")
        errors = validate_goal_artifact_graph_payload(graph)
        if errors:
            raise ValueError(f"goal_artifact_graph_invalid:{'; '.join(errors)}")
        path = self._path_for_goal(goal_id)
        path.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return graph
