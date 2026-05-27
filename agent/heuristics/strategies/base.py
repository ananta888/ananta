"""Base interface for Python HeuristicStrategies.

All Python strategies must:
  - Subclass HeuristicStrategyBase
  - Implement evaluate(context, definition) -> DecisionResult
  - Access context ONLY via provided ports (no direct DB/file/network calls)
  - Be deterministic (no LLM calls)
  - Be importable from an allowlisted module

Scoring utilities are provided in scoring.py.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition


# ── Ports ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ArtifactRefPort:
    """Read-only port for artifact references."""
    selected_artifacts: tuple[str, ...] = ()
    active_goal_id: str | None = None
    active_task_id: str | None = None

    @staticmethod
    def from_context(ctx: DecisionContext) -> "ArtifactRefPort":
        return ArtifactRefPort(
            selected_artifacts=tuple(ctx.selected_artifacts or []),
            active_goal_id=ctx.active_goal_id,
            active_task_id=ctx.active_task_id,
        )


@dataclass(frozen=True)
class CodeCompassReadPort:
    """Read-only port for CodeCompass/sourcepack refs."""
    allowed_source_scopes: tuple[str, ...] = ()

    @staticmethod
    def from_context(ctx: DecisionContext) -> "CodeCompassReadPort":
        return CodeCompassReadPort(
            allowed_source_scopes=tuple(ctx.allowed_source_scopes or []),
        )


@dataclass(frozen=True)
class UiMotionPort:
    """Read-only port for UI state relevant to snake motion."""
    active_panel: str | None = None
    recent_event_types: tuple[str, ...] = ()

    @staticmethod
    def from_context(ctx: DecisionContext) -> "UiMotionPort":
        recent = tuple(
            str(e.get("event_type") or e.get("kind") or "")
            for e in (ctx.recent_events or [])
        )
        return UiMotionPort(
            active_panel=ctx.active_panel,
            recent_event_types=recent,
        )


@dataclass(frozen=True)
class HelpcenterReadPort:
    """Read-only port for helpcenter scope refs."""
    helpcenter_scopes: tuple[str, ...] = ()

    @staticmethod
    def from_context(ctx: DecisionContext) -> "HelpcenterReadPort":
        scopes = tuple(
            s for s in (ctx.allowed_source_scopes or [])
            if "helpcenter" in s.lower()
        )
        return HelpcenterReadPort(helpcenter_scopes=scopes)


@dataclass(frozen=True)
class TodoReadPort:
    """Read-only port for todo/task refs."""
    todo_scopes: tuple[str, ...] = ()

    @staticmethod
    def from_context(ctx: DecisionContext) -> "TodoReadPort":
        scopes = tuple(
            s for s in (ctx.allowed_source_scopes or [])
            if "todo" in s.lower() or "task" in s.lower()
        )
        return TodoReadPort(todo_scopes=scopes)


@dataclass(frozen=True)
class ClockPort:
    """Read-only access to timestamps."""
    now: float = 0.0


# ── Base strategy ─────────────────────────────────────────────────────────────

class HeuristicStrategyBase(abc.ABC):
    """All Python heuristic strategies must subclass this."""

    @property
    def strategy_id(self) -> str:
        return self.__class__.__name__

    @abc.abstractmethod
    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        """Evaluate the context and return a DecisionResult. Must be deterministic."""
        ...

    def domain(self) -> str:
        return ""
