"""DecisionContext — gemeinsames Kontext-Modell für Snake und Chat Heuristiken.

Nutzt ai_snake_context.py und ai_snake_observation.py als Basis.
Schema: schemas/heuristic/decision_context.v1.json
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
        }


def build_from_tui_state(
    *,
    tui_state: dict[str, Any] | None = None,
    observation_buffer: Any = None,
    ai_status: str = "available",
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
