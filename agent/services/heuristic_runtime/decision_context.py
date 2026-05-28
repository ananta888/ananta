"""DecisionContext — gemeinsames Kontext-Modell für Snake und Chat Heuristiken.

Nutzt ai_snake_context.py und ai_snake_observation.py als Basis.
Schema: schemas/heuristic/decision_context.v1.json
Schema v2: schemas/heuristic/decision_context.v2.json
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DecisionContext:
    source_surface: str
    ai_status: str = "available"
    active_goal_id: str | None = None
    active_task_id: str | None = None
    selected_artifacts: list[str] = field(default_factory=list)
    active_panel: str | None = None
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    allowed_source_scopes: list[str] = field(default_factory=list)
    policy_state: str | None = None
    query: str | None = None
    # v2: TUI snapshot references (optional, rückwärtskompatibel)
    tui_snapshot_ref: str | None = None   # screen_hash des aktuellen Snapshots
    tui_delta_ref: str | None = None      # "prev_hash:curr_hash" kompakt
    semantic_hash: str | None = None      # Hash des SemanticOverlay
    semantic_panel: str | None = None     # aktives Panel aus SemanticOverlay
    snake_head_x: int | None = None       # aktuelle Snake-Head X Position (optional, v2+)
    snake_head_y: int | None = None       # aktuelle Snake-Head Y Position (optional, v2+)

    @property
    def context_hash(self) -> str:
        relevant = {
            "surface": self.source_surface,
            "goal": self.active_goal_id,
            "task": self.active_task_id,
            "artifacts": sorted(self.selected_artifacts),
            "panel": self.active_panel,
            "scopes": sorted(self.allowed_source_scopes),
            "ai_status": self.ai_status,
            "query": self.query,
            # v2: include snapshot/semantic references in hash (not volatile timestamps)
            "tui_snapshot_ref": self.tui_snapshot_ref,
            "semantic_hash": self.semantic_hash,
            "snake_head_x": self.snake_head_x,
            "snake_head_y": self.snake_head_y,
        }
        payload = json.dumps(relevant, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_surface": self.source_surface,
            "ai_status": self.ai_status,
            "active_goal_id": self.active_goal_id,
            "active_task_id": self.active_task_id,
            "selected_artifacts": list(self.selected_artifacts),
            "active_panel": self.active_panel,
            "recent_events": list(self.recent_events),
            "allowed_source_scopes": list(self.allowed_source_scopes),
            "policy_state": self.policy_state,
            "query": self.query,
            "context_hash": self.context_hash,
            # v2 fields
            "tui_snapshot_ref": self.tui_snapshot_ref,
            "tui_delta_ref": self.tui_delta_ref,
            "semantic_hash": self.semantic_hash,
            "semantic_panel": self.semantic_panel,
            "snake_head_x": self.snake_head_x,
            "snake_head_y": self.snake_head_y,
        }


def build_from_tui_state(
    *,
    tui_state: dict[str, Any] | None = None,
    observation_buffer: Any = None,
    ai_status: str = "available",
    snapshot_ref: str | None = None,
    delta_ref: str | None = None,
    semantic_hash: str | None = None,
    semantic_panel: str | None = None,
) -> DecisionContext:
    """Erstellt DecisionContext aus TUI-Zustand.

    Basiert auf ai_snake_context.py default_ai_context() und
    ai_snake_observation.py ObservationBuffer.
    """
    state = dict(tui_state or {})

    active_goal_id = str(state.get("active_goal_id") or "").strip() or None
    active_task_id = str(state.get("active_task_id") or "").strip() or None
    active_panel = str(state.get("active_panel") or "").strip() or None

    selected_artifacts = [
        str(r).strip() for r in (state.get("selected_artifacts") or [])
        if str(r).strip()
    ][:5]

    allowed_scopes = [
        str(s).strip() for s in (state.get("allowed_source_scopes") or [])
        if str(s).strip()
    ]

    recent_events: list[dict[str, Any]] = []
    if observation_buffer is not None and hasattr(observation_buffer, "events"):
        for ev in observation_buffer.events()[-20:]:
            recent_events.append({
                "event_id": ev.event_id,
                "kind": ev.kind,
                "normalized_value": str(ev.normalized_value or "")[:200],
                "ref_id": ev.ref_id,
                "timestamp": ev.timestamp,
            })

    policy_state = str(state.get("policy_state") or "").strip() or None
    snake_head_x, snake_head_y = _extract_snake_head(state)

    return DecisionContext(
        source_surface="tui_snake",
        ai_status=ai_status,
        active_goal_id=active_goal_id,
        active_task_id=active_task_id,
        selected_artifacts=selected_artifacts,
        active_panel=active_panel,
        recent_events=recent_events,
        allowed_source_scopes=allowed_scopes,
        policy_state=policy_state,
        tui_snapshot_ref=snapshot_ref,
        tui_delta_ref=delta_ref,
        semantic_hash=semantic_hash,
        semantic_panel=semantic_panel,
        snake_head_x=snake_head_x,
        snake_head_y=snake_head_y,
    )


def build_from_chat_state(
    *,
    chat_state: dict[str, Any] | None = None,
    ai_status: str = "available",
) -> DecisionContext:
    """Erstellt DecisionContext aus Chat-Zustand."""
    state = dict(chat_state or {})

    return DecisionContext(
        source_surface="chat_codecompass",
        ai_status=ai_status,
        active_goal_id=str(state.get("active_goal_id") or "").strip() or None,
        active_task_id=str(state.get("active_task_id") or "").strip() or None,
        selected_artifacts=[
            str(r).strip() for r in (state.get("selected_artifacts") or [])
            if str(r).strip()
        ][:5],
        active_panel=str(state.get("active_panel") or "").strip() or None,
        recent_events=[],
        allowed_source_scopes=[
            str(s).strip() for s in (state.get("allowed_source_scopes") or [])
            if str(s).strip()
        ],
        policy_state=str(state.get("policy_state") or "").strip() or None,
        query=str(state.get("query") or "").strip() or None,
    )


def _extract_snake_head(state: dict[str, Any]) -> tuple[int | None, int | None]:
    """Best-effort extraction of the local snake head from TUI state."""
    direct_head = state.get("snake_head")
    if isinstance(direct_head, (list, tuple)) and len(direct_head) >= 2:
        return int(direct_head[0]), int(direct_head[1])

    snake = state.get("snake")
    if isinstance(snake, list) and snake:
        first = snake[0]
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            return int(first[0]), int(first[1])

    game = state.get("header_logo_game")
    if isinstance(game, dict):
        local_id = str(game.get("local_snake_id") or "s1")
        snakes_raw = game.get("snakes")
        if isinstance(snakes_raw, dict):
            local = snakes_raw.get(local_id)
            if isinstance(local, dict):
                local_snake = local.get("snake")
                if isinstance(local_snake, list) and local_snake:
                    first = local_snake[0]
                    if isinstance(first, (list, tuple)) and len(first) >= 2:
                        return int(first[0]), int(first[1])
        game_snake = game.get("snake")
        if isinstance(game_snake, list) and game_snake:
            first = game_snake[0]
            if isinstance(first, (list, tuple)) and len(first) >= 2:
                return int(first[0]), int(first[1])

    return None, None
