"""Policy and Security hints for Visual Process steps (VPAD-008).

Classifies each step in a graph with policy hints such as
'requires_approval', 'read_only', 'mutates_production', 'high_risk'.
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
HINT_VECTOR_OP          = "vector_operation"
HINT_ML_INFERENCE       = "ml_inference"
HINT_QUANTIZATION       = "quantization"
HINT_SELF_MODIFYING     = "self_modifying"
HINT_RETRIEVAL          = "retrieval"
HINT_INDEX_WRITE        = "index_write"
HINT_EVOLUTION          = "evolution"


# ── Kind → hint rules ─────────────────────────────────────────────────────────

_KIND_HINTS: dict[str, list[str]] = {
    # Legacy kinds (kept for backward compat)
    "deploy":       [HINT_REQUIRES_APPROVAL, HINT_MUTATES_PRODUCTION, HINT_HIGH_RISK],
    "infra":        [HINT_REQUIRES_APPROVAL, HINT_HIGH_RISK, HINT_SHELL_EXEC],
    "ci":           [HINT_SHELL_EXEC, HINT_RUNS_TESTS],
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
    # Worker – mutation
    "patch_apply":     [HINT_WRITES_FILES, HINT_LLM_CALL],
    "patch_propose":   [HINT_LLM_CALL, HINT_READ_ONLY],
    "command_execute": [HINT_SHELL_EXEC, HINT_HIGH_RISK],
    "shell_execute":   [HINT_SHELL_EXEC, HINT_HIGH_RISK],
    "shell_execution": [HINT_SHELL_EXEC, HINT_HIGH_RISK],
    # Worker – LLM readonly
    "plan_only":        [HINT_LLM_CALL, HINT_READ_ONLY],
    "review":           [HINT_LLM_CALL, HINT_READ_ONLY],
    "summarize":        [HINT_LLM_CALL],
    "research_limited": [HINT_READ_ONLY, HINT_LLM_CALL],
    # Worker – deterministic
    "run_tests":   [HINT_RUNS_TESTS],
    "script":      [],
    "git_op":      [],
    "file_check":  [HINT_READ_ONLY],
    "regex_check": [HINT_READ_ONLY],
    # Control flow
    "fork":     [],
    "join":     [],
    "approval": [HINT_REQUIRES_APPROVAL],
    "parallel": [],
    # Worker – workspace diff (WorkspaceDiffService — deterministic, fully implemented)
    "workspace_snapshot": [HINT_READ_ONLY],
    "workspace_diff":     [HINT_READ_ONLY, HINT_WRITES_FILES],  # writes manifest, not source

    # Retrieval / CodeCompass (19 modules — fully implemented)
    "codecompass_index_build":    [HINT_INDEX_WRITE, HINT_RETRIEVAL],
    "codecompass_vector_search":  [HINT_READ_ONLY, HINT_RETRIEVAL],
    "codecompass_fts_search":     [HINT_READ_ONLY, HINT_RETRIEVAL],
    "codecompass_graph_expand":   [HINT_READ_ONLY, HINT_RETRIEVAL],

    # ML – Embedding (HTTP API or hash — no local PyTorch)
    "embed_api":   [HINT_VECTOR_OP, HINT_NETWORK_EGRESS, HINT_READ_ONLY],
    "embed_chunk": [HINT_VECTOR_OP, HINT_NETWORK_EGRESS, HINT_READ_ONLY],

    # ML – TurboQuant (TQ-011/012 implemented; TQ-013 ProdStub = NotImplementedError)
    "sign_rotation":  [HINT_VECTOR_OP, HINT_READ_ONLY],
    "turboquant_mse": [HINT_VECTOR_OP, HINT_QUANTIZATION, HINT_READ_ONLY],

    # ML – RAG
    "rag_retrieve":  [HINT_READ_ONLY, HINT_RETRIEVAL],
    "rerank":        [HINT_READ_ONLY, HINT_RETRIEVAL],
    "query_rewrite": [HINT_READ_ONLY],  # rule-based synonym expansion, no LLM, no network

    # ML – Evolution (EvolutionService — fully implemented)
    "evolution_analyze":  [HINT_LLM_CALL, HINT_EVOLUTION, HINT_READ_ONLY],
    "evolution_validate": [HINT_EVOLUTION, HINT_READ_ONLY],
    "evolution_apply":    [HINT_LLM_CALL, HINT_EVOLUTION, HINT_WRITES_FILES, HINT_SELF_MODIFYING, HINT_REQUIRES_APPROVAL],

    # ML – Prompt / Project Evolution
    "evolve_prompt":  [HINT_LLM_CALL, HINT_WRITES_FILES, HINT_SELF_MODIFYING],
    "evolve_project": [HINT_LLM_CALL, HINT_SELF_MODIFYING, HINT_HIGH_RISK],

    # ML – Clustering (deterministisch, rag-helper — Leiden/Louvain nicht in prod)
    "domain_cluster": [HINT_READ_ONLY],

    # Backward-compat (old unified kinds)
    "vector_encode":     [HINT_VECTOR_OP, HINT_NETWORK_EGRESS, HINT_READ_ONLY],
    "turboquant_encode": [HINT_VECTOR_OP, HINT_QUANTIZATION, HINT_READ_ONLY],
    "cluster":           [HINT_READ_ONLY],
}


def classify_step(step: VisualProcessStep) -> list[str]:
    """Return the full set of policy hints for a step (kind-based + explicit)."""
    hints: set[str] = set(step.policy_hints)
    hints.update(_KIND_HINTS.get(step.kind, []))
    if step.gate:
        hints.add(HINT_REQUIRES_APPROVAL)
    # evolve_project with apply_allowed escalates to requires_approval
    if step.kind == "evolve_project" and step.metadata.get("apply_allowed"):
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
        "has_ml_inference": HINT_ML_INFERENCE in all_hints,
        "has_self_modifying": HINT_SELF_MODIFYING in all_hints,
        "has_high_risk": HINT_HIGH_RISK in all_hints,
    }
