"""TaskClassResolver (te-003) — maps task_kind + capabilities to execution class.

Combines task_kind rules with TaskIntentRouter output to produce the
definitive task_class/llm_required verdict stored in TaskRoutingContract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.task_intent_router import (
    DETERMINISTIC_INTENTS,
    HYBRID_INTENTS,
    IntentResult,
    TaskIntentRouter,
)

# task_kind → forced class (overrides intent heuristics)
_KIND_OVERRIDES: dict[str, str] = {
    # always deterministic
    "list_files":      "deterministic",
    "read_file":       "deterministic",
    "grep_search":     "deterministic",
    "git_status":      "deterministic",
    "git_diff":        "deterministic",
    "json_validate":   "deterministic",
    "schema_validate": "deterministic",
    # hybrid
    "run_tests":       "hybrid",
    # always LLM
    "llm_generate":    "llm_required",
    "code_review":     "llm_required",
    "goal_plan":       "llm_required",
    "goal_propose":    "llm_required",
    "default":         "llm_required",
}

# Capabilities that force a downgrade to llm_required regardless of kind
_LLM_FORCE_CAPABILITIES: frozenset[str] = frozenset({
    "write_file",
    "shell_exec",
    "database_write",
    "deploy",
    "send_notification",
})


@dataclass(frozen=True)
class ClassResolverResult:
    task_class: str           # "deterministic" | "hybrid" | "llm_required"
    llm_required: bool
    intent: str
    deterministic_handler_id: str | None
    reason: str               # short human-readable explanation


class TaskClassResolver:
    """Resolve the execution class for a task.

    Combines:
    1. ``_KIND_OVERRIDES`` — hard-coded per kind
    2. capability-based forced LLM upgrade
    3. TaskIntentRouter heuristics on tool_calls / command

    Usage::

        resolver = TaskClassResolver()
        result = resolver.resolve(task)
        routing.task_class = result.task_class
        routing.llm_required = result.llm_required
    """

    def __init__(self) -> None:
        self._intent_router = TaskIntentRouter()

    def resolve(self, task: dict[str, Any]) -> ClassResolverResult:
        kind = (task.get("task_kind") or task.get("kind") or "").strip().lower()
        capabilities: list[str] = [
            str(c).strip().lower()
            for c in (task.get("required_capabilities") or [])
        ]

        # Capability-forced LLM upgrade
        forced_cap = _LLM_FORCE_CAPABILITIES.intersection(capabilities)
        if forced_cap:
            cap_str = ", ".join(sorted(forced_cap))
            return ClassResolverResult(
                task_class="llm_required",
                llm_required=True,
                intent="llm_unknown",
                deterministic_handler_id=None,
                reason=f"capability_forces_llm:{cap_str}",
            )

        # Kind override
        if kind in _KIND_OVERRIDES:
            forced_class = _KIND_OVERRIDES[kind]
            ir = self._intent_router.route(task)
            handler_id = ir.deterministic_handler_id if forced_class != "llm_required" else None
            return ClassResolverResult(
                task_class=forced_class,
                llm_required=(forced_class == "llm_required"),
                intent=ir.intent,
                deterministic_handler_id=handler_id,
                reason=f"kind_override:{kind}",
            )

        # Intent-router heuristics
        ir: IntentResult = self._intent_router.route(task)
        return ClassResolverResult(
            task_class=ir.task_class,
            llm_required=ir.llm_required,
            intent=ir.intent,
            deterministic_handler_id=ir.deterministic_handler_id,
            reason=f"intent_router:{ir.source}",
        )
