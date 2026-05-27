"""EclipseSnakeDecisionAdapter — Python bridge for AnantaSnakePredictionRuntime.

The Java side sends zone-classified intent messages (JSON) via Hub API relay.
This adapter:
  1. Parses the Java intent message (no raw file content — zone names only)
  2. Builds a DecisionContext suitable for DefaultEclipseSnakeStrategy
  3. Runs SnakeDecisionManager.decide()
  4. Returns EclipseCommandAdapter.flush() for Hub relay back to Java

Java ↔ Python protocol contract:
  IN:  {"intent": "follow"|"lurk"|"idle", "zone": str, "dx": int, "dy": int,
        "ttl_millis": int, "context_hash": str}
  OUT: list of command dicts (same as EclipseCommandAdapter.flush())

No file contents cross this boundary — only zone classification strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.heuristic_commands import (
    EclipseCommandAdapter,
    command_for_decision,
)
from agent.services.heuristic_runtime.snake_decision_manager import SnakeDecisionManager


@dataclass
class EclipseSnakeIntent:
    """Parsed intent message from AnantaSnakePredictionRuntime.java."""
    intent: str              # follow | lurk | idle
    zone: str                # e.g. "editor_active", "diff_panel", "chat_focus"
    dx: int = 0
    dy: int = 0
    ttl_millis: int = 7000   # maps to lease TTL; default 7s
    context_hash: str = ""
    panel_hint: str = ""     # optional: which Eclipse panel is active

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "EclipseSnakeIntent":
        return EclipseSnakeIntent(
            intent=str(data.get("intent") or "idle").strip().lower(),
            zone=str(data.get("zone") or "").strip(),
            dx=int(data.get("dx") or 0),
            dy=int(data.get("dy") or 0),
            ttl_millis=int(data.get("ttl_millis") or 7000),
            context_hash=str(data.get("context_hash") or ""),
            panel_hint=str(data.get("panel_hint") or ""),
        )

    def to_context_event(self) -> dict[str, Any]:
        return {
            "event_type": "panel_switch" if self.panel_hint else "pointer_move",
            "normalized_value": self.zone,
            "surface": "eclipse_snake",
            "ref_id": self.panel_hint or None,
        }


class EclipseSnakeDecisionAdapter:
    """Bridges Eclipse Java runtime to Python SnakeDecisionManager.

    Call process_intent() for each intent received from the Java side.
    Returns a list of command dicts to relay back to the Eclipse plugin.
    """

    def __init__(self, manager: SnakeDecisionManager | None = None) -> None:
        self._manager = manager or SnakeDecisionManager()
        self._last_context_hash: str = ""

    def process_intent(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """Process one intent message from AnantaSnakePredictionRuntime.

        Returns command dicts for Hub API relay to Eclipse plugin.
        Offline/slow Hub does NOT block this path — runs fully local.
        """
        intent = EclipseSnakeIntent.from_dict(message)

        ctx = self._build_context(intent)
        result = self._manager.decide(ctx)

        adapter = EclipseCommandAdapter()
        cmd = command_for_decision(result)
        cmd.execute(adapter)

        self._last_context_hash = intent.context_hash
        return adapter.flush()

    def _build_context(self, intent: EclipseSnakeIntent) -> DecisionContext:
        goal_id = intent.zone if intent.intent == "follow" else None
        recent_events = [intent.to_context_event()]
        return DecisionContext(
            source_surface="eclipse_snake",
            active_goal_id=goal_id,
            recent_events=recent_events,
            ai_status="offline",  # Eclipse snake always runs local-deterministic
        )

    @property
    def last_context_hash(self) -> str:
        return self._last_context_hash


def parse_eclipse_ttl_millis(ttl_millis: int) -> float:
    """Convert Java ttlMillis to lease TTL seconds, clamped to snake domain range."""
    seconds = ttl_millis / 1000.0
    return max(5.0, min(10.0, seconds))
