"""TaskEnginePolicyGate (te-009 + te-010).

Central policy enforcement before any task execution path.

Checks (in order):
1. task_engine_enabled — if False, everything passes through to LLM unchanged.
2. task_engine_deterministic_bypass_enabled — if False, all tasks go to LLM even
   if a deterministic handler exists.
3. Unknown-tool-policy (te-010): if strict_unknown_tool_policy=True and the task
   contains tool_calls with unknown tool names, block execution.
4. Combines TaskClassResolver result with config to produce a final GateDecision.

Usage::

    gate = TaskEnginePolicyGate.from_settings()
    decision = gate.evaluate(task)
    if decision.bypass_llm and decision.handler_id:
        # run deterministic handler
    elif not decision.allow:
        # blocked — raise or return error
    else:
        # proceed to LLM
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.task_class_resolver import TaskClassResolver


# ── Known tool names (te-010) ─────────────────────────────────────────────────
# Tools that have a registered deterministic handler or are known safe.
# Tasks with tool_calls outside this set are flagged as "unknown_tool" in strict mode.

KNOWN_TOOLS: frozenset[str] = frozenset({
    "list_files", "list_directory", "ls",
    "read_file", "cat_file", "view_file", "file_read",
    "grep_search", "search_files", "grep", "ripgrep",
    "git_status", "git_diff",
    "json_validate", "validate_json",
    "schema_validate", "validate_schema",
    "run_tests", "pytest", "run_pytest",
    # LLM tools — always valid, just require LLM
    "llm_generate", "code_review", "goal_plan", "goal_propose",
    "write_file", "shell_exec", "database_write",
})


@dataclass(frozen=True)
class GateDecision:
    allow: bool
    bypass_llm: bool            # True → run deterministic handler instead of LLM
    handler_id: str | None      # handler to use when bypass_llm is True
    task_class: str             # "deterministic" | "hybrid" | "llm_required"
    intent: str
    llm_required: bool
    reason: str
    unknown_tools: list[str]    # tools not in KNOWN_TOOLS (non-empty only in strict mode)
    blocked: bool               # hard block — should not proceed at all


class TaskEnginePolicyGate:
    def __init__(
        self,
        *,
        enabled: bool = True,
        deterministic_bypass_enabled: bool = True,
        strict_unknown_tool_policy: bool = False,
    ) -> None:
        self._enabled = enabled
        self._bypass_enabled = deterministic_bypass_enabled
        self._strict_unknown = strict_unknown_tool_policy
        self._resolver = TaskClassResolver()

    @classmethod
    def from_settings(cls) -> "TaskEnginePolicyGate":
        try:
            from agent.config import settings
            return cls(
                enabled=settings.task_engine_enabled,
                deterministic_bypass_enabled=settings.task_engine_deterministic_bypass_enabled,
                strict_unknown_tool_policy=settings.task_engine_strict_unknown_tool_policy,
            )
        except Exception:
            return cls()  # safe defaults

    def evaluate(self, task: dict[str, Any]) -> GateDecision:
        # Gate disabled → pass everything through to LLM unchanged
        if not self._enabled:
            return GateDecision(
                allow=True,
                bypass_llm=False,
                handler_id=None,
                task_class="llm_required",
                intent="llm_unknown",
                llm_required=True,
                reason="task_engine_disabled",
                unknown_tools=[],
                blocked=False,
            )

        # Unknown-tool check (te-010)
        unknown_tools = self._unknown_tools(task) if self._strict_unknown else []
        if unknown_tools:
            return GateDecision(
                allow=False,
                bypass_llm=False,
                handler_id=None,
                task_class="llm_required",
                intent="llm_unknown",
                llm_required=True,
                reason=f"strict_unknown_tool_policy:blocked:{','.join(unknown_tools)}",
                unknown_tools=unknown_tools,
                blocked=True,
            )

        cr = self._resolver.resolve(task)

        # Deterministic bypass disabled → route everything to LLM
        if not self._bypass_enabled:
            return GateDecision(
                allow=True,
                bypass_llm=False,
                handler_id=None,
                task_class=cr.task_class,
                intent=cr.intent,
                llm_required=True,
                reason="deterministic_bypass_disabled",
                unknown_tools=[],
                blocked=False,
            )

        # LLM required → allow, but don't bypass
        if cr.llm_required:
            return GateDecision(
                allow=True,
                bypass_llm=False,
                handler_id=None,
                task_class=cr.task_class,
                intent=cr.intent,
                llm_required=True,
                reason=cr.reason,
                unknown_tools=[],
                blocked=False,
            )

        # Deterministic or hybrid → bypass LLM
        return GateDecision(
            allow=True,
            bypass_llm=True,
            handler_id=cr.deterministic_handler_id,
            task_class=cr.task_class,
            intent=cr.intent,
            llm_required=False,
            reason=cr.reason,
            unknown_tools=[],
            blocked=False,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _unknown_tools(task: dict[str, Any]) -> list[str]:
        tool_calls = task.get("tool_calls") or []
        unknown: list[str] = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                name = (tc.get("name") or tc.get("tool") or tc.get("function", {}).get("name") or "").lower()
            elif isinstance(tc, str):
                name = tc.lower()
            else:
                continue
            if name and name not in KNOWN_TOOLS:
                unknown.append(name)
        return unknown
