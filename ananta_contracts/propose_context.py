"""Shared propose-strategy context (CLN-056)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ananta_contracts.propose_policy import ProposePolicy


@dataclass
class ProposeContext:
    """Context passed to every propose strategy."""

    goal_id: str
    task_id: str
    task: dict[str, Any]
    base_prompt: str
    research_context: dict[str, Any] | None = None
    cli_runner: Callable | None = None
    tool_definitions_resolver: Callable | None = None
    policy: ProposePolicy | None = None  # T003: strategies may read policy
    effective_config: dict[str, Any] | None = None  # CPR-003: goal-scoped config passed through
    instruction_stack: dict[str, Any] | None = None
    rendered_system_prompt: str | None = None
    instruction_diagnostics: dict[str, Any] | None = None
    planning_context_compaction: dict[str, Any] | None = None
    planning_context_compaction_meta: dict[str, Any] | None = None
    # APRL-011: active agent profile metadata; non-overridable by strategies
    active_agent_profile: dict[str, Any] | None = None


__all__ = ["ProposeContext"]
