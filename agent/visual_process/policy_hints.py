"""Policy and Security hints for Visual Process steps (VPAD-008).

Classifies each step in a graph with policy hints such as
'requires_approval', 'read_only', 'mutates_production', 'high_risk'.

The hints are stored on VisualProcessStep.policy_hints and surfaced
in the graph editor and the API validate response.
"""
from __future__ import annotations

from typing import Any

from agent.visual_process.models import VisualProcessGraph, VisualProcessStep


# ── Hint vocabulary ───────────────────────────────────────────────────────────

HINT_REQUIRES_APPROVAL  = "requires_approval"
HINT_READ_ONLY          = "read_only"
HINT_MUTATES_PRODUCTION = "mutates_production"
HINT_HIGH_RISK          = "high_risk"
HINT_SHELL_EXEC         = "shell_exec"
HINT_NETWORK_EGRESS     = "network_egress"
HINT_LLM_CALL           = "llm_call"
HINT_WRITES_FILES       = "writes_files"
HINT_RUNS_TESTS         = "runs_tests"


# ── Kind → hint rules ─────────────────────────────────────────────────────────

_KIND_HINTS: dict[str, list[str]] = {
    "deploy":       [HINT_REQUIRES_APPROVAL, HINT_MUTATES_PRODUCTION, HINT_HIGH_RISK],
    "infra":        [HINT_REQUIRES_APPROVAL, HINT_HIGH_RISK, HINT_SHELL_EXEC],
    "ci":           [HINT_SHELL_EXEC, HINT_RUNS_TESTS],
    "run_tests":    [HINT_RUNS_TESTS],
    "coding":       [HINT_WRITES_FILES, HINT_LLM_CALL],
    "refactor":     [HINT_WRITES_FILES, HINT_LLM_CALL],
    "bugfix":       [HINT_WRITES_FILES, HINT_LLM_CALL],
    "llm_generate": [HINT_LLM_CALL],
    "goal_plan":    [HINT_LLM_CALL],
    "goal_propose": [HINT_LLM_CALL],
    "code_review":  [HINT_LLM_CALL, HINT_READ_ONLY],
    "analysis":     [HINT_READ_ONLY],
    "research":     [HINT_READ_ONLY, HINT_NETWORK_EGRESS],
    "read_file":    [HINT_READ_ONLY],
    "grep_search":  [HINT_READ_ONLY],
    "list_files":   [HINT_READ_ONLY],
    "git_status":   [HINT_READ_ONLY],
    "git_diff":     [HINT_READ_ONLY],
}


def classify_step(step: VisualProcessStep) -> list[str]:
    """Return the full set of policy hints for a step (kind-based + explicit)."""
    hints: set[str] = set(step.policy_hints)
    hints.update(_KIND_HINTS.get(step.kind, []))
    if step.gate:
        hints.add(HINT_REQUIRES_APPROVAL)
    return sorted(hints)


def annotate_graph(graph: VisualProcessGraph) -> VisualProcessGraph:
    """Return a copy of the graph with policy_hints filled in for all steps."""
    new_steps = []
    for step in graph.steps:
        hints = classify_step(step)
        new_steps.append(step.model_copy(update={"policy_hints": hints}))
    return graph.model_copy(update={"steps": new_steps})


def policy_summary(graph: VisualProcessGraph) -> dict[str, Any]:
    """Aggregate policy overview for the whole graph."""
    all_hints: set[str] = set()
    gate_steps: list[str] = []
    high_risk_steps: list[str] = []
    for step in graph.steps:
        hints = set(classify_step(step))
        all_hints |= hints
        if step.gate or HINT_REQUIRES_APPROVAL in hints:
            gate_steps.append(step.id)
        if HINT_HIGH_RISK in hints or HINT_MUTATES_PRODUCTION in hints:
            high_risk_steps.append(step.id)
    return {
        "all_hints": sorted(all_hints),
        "gate_steps": gate_steps,
        "high_risk_steps": high_risk_steps,
        "requires_approval": bool(gate_steps),
        "has_llm_calls": HINT_LLM_CALL in all_hints,
        "has_shell_exec": HINT_SHELL_EXEC in all_hints,
        "mutates_production": HINT_MUTATES_PRODUCTION in all_hints,
    }
