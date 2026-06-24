"""Canonical task kind registry (VPWRK-001).

Single source of truth for all valid task_kinds in the Visual Process Designer.
Separates Worker-dispatchable kinds from ML/AI-pipeline kinds.
"""
from __future__ import annotations

from typing import TypedDict


WORKER_TASK_KINDS: frozenset[str] = frozenset({
    "patch_apply", "patch_propose", "command_execute", "shell_execute",
    "shell_execution", "plan_only", "review", "summarize", "research_limited",
    "run_tests", "script", "git_op", "file_check", "regex_check",
    "fork", "join", "approval",
})

ML_TASK_KINDS: frozenset[str] = frozenset({
    "vector_encode", "turboquant_encode", "embed_chunk",
    "rag_retrieve", "rerank", "query_rewrite",
    "cluster", "evolve_prompt", "evolve_project",
})

# Legacy VP-Editor kinds → canonical Worker kinds
LEGACY_MAP: dict[str, str] = {
    "coding":       "patch_propose",
    "analysis":     "review",
    "code_review":  "review",
    "llm_generate": "summarize",
    "deploy":       "shell_execute",
    "research":     "research_limited",
    "refactor":     "patch_apply",
    "goal_plan":    "plan_only",
    "goal_propose": "plan_only",
    "bugfix":       "patch_apply",
    "testing":      "run_tests",
    "write_tests":  "run_tests",
    "read_file":    "file_check",
    "grep_search":  "script",
    "git_status":   "git_op",
    "git_diff":     "git_op",
    "parallel":     "fork",
    "infra":        "shell_execute",
    "ci":           "run_tests",
    "list_files":   "file_check",
}

ALL_TASK_KINDS: frozenset[str] = WORKER_TASK_KINDS | ML_TASK_KINDS

_CONTROL_FLOW_KINDS: frozenset[str] = frozenset({"fork", "join", "approval"})


class TaskKindInfo(TypedDict):
    id: str
    label: str
    group: str          # "control_flow" | "worker" | "ml"
    dispatch_capable: bool
    description: str


_KIND_INFO: dict[str, TaskKindInfo] = {
    # ── Control flow ───────────────────────────────────────────────────────────
    "fork":      {"id": "fork",      "label": "Fork (Parallel)",     "group": "control_flow", "dispatch_capable": True,  "description": "Splits execution into parallel branches"},
    "join":      {"id": "join",      "label": "Join (Sync)",         "group": "control_flow", "dispatch_capable": True,  "description": "Waits for all parallel branches to complete"},
    "approval":  {"id": "approval",  "label": "Approval Gate",       "group": "control_flow", "dispatch_capable": True,  "description": "Pauses workflow for human approval"},
    # ── Worker – mutation ──────────────────────────────────────────────────────
    "patch_apply":    {"id": "patch_apply",    "label": "Patch Anwenden",       "group": "worker", "dispatch_capable": True,  "description": "Applies a code patch to the workspace"},
    "patch_propose":  {"id": "patch_propose",  "label": "Patch Vorschlagen",    "group": "worker", "dispatch_capable": True,  "description": "LLM generates a code patch proposal"},
    "command_execute":{"id": "command_execute","label": "Befehl Ausführen",     "group": "worker", "dispatch_capable": True,  "description": "Executes a deterministic command"},
    "shell_execute":  {"id": "shell_execute",  "label": "Shell Ausführen",      "group": "worker", "dispatch_capable": True,  "description": "Runs an arbitrary shell command"},
    # ── Worker – LLM readonly ──────────────────────────────────────────────────
    "plan_only":        {"id": "plan_only",        "label": "Planen (LLM)",          "group": "worker", "dispatch_capable": True,  "description": "LLM planning pass, no mutations"},
    "review":           {"id": "review",           "label": "Review (LLM)",           "group": "worker", "dispatch_capable": True,  "description": "LLM code or doc review"},
    "summarize":        {"id": "summarize",        "label": "Zusammenfassen (LLM)",   "group": "worker", "dispatch_capable": True,  "description": "LLM text summarization"},
    "research_limited": {"id": "research_limited", "label": "Recherche (begrenzt)",   "group": "worker", "dispatch_capable": True,  "description": "Limited research without network egress"},
    # ── Worker – deterministic ─────────────────────────────────────────────────
    "run_tests":   {"id": "run_tests",   "label": "Tests Ausführen",  "group": "worker", "dispatch_capable": True,  "description": "Runs the project test suite"},
    "script":      {"id": "script",      "label": "Script",           "group": "worker", "dispatch_capable": True,  "description": "Runs a deterministic script"},
    "git_op":      {"id": "git_op",      "label": "Git Operation",    "group": "worker", "dispatch_capable": True,  "description": "Performs a git operation"},
    "file_check":  {"id": "file_check",  "label": "Datei Prüfen",     "group": "worker", "dispatch_capable": True,  "description": "Checks file existence or content"},
    "regex_check": {"id": "regex_check", "label": "Regex Prüfen",     "group": "worker", "dispatch_capable": True,  "description": "Regex-based file content check"},
    # ── ML / AI ────────────────────────────────────────────────────────────────
    "vector_encode":    {"id": "vector_encode",    "label": "Vektor-Encoding",        "group": "ml", "dispatch_capable": False, "description": "Encodes text via transformer encoder (no LLM prompt)"},
    "turboquant_encode":{"id": "turboquant_encode","label": "TurboQuant Komprimierung","group": "ml", "dispatch_capable": False, "description": "Quantizes float32 vectors to lower bit-width (experimental)"},
    "embed_chunk":      {"id": "embed_chunk",      "label": "Chunk + Einbetten",      "group": "ml", "dispatch_capable": False, "description": "Chunks text documents and embeds each chunk"},
    "rag_retrieve":     {"id": "rag_retrieve",     "label": "RAG Abruf",              "group": "ml", "dispatch_capable": False, "description": "Retrieves candidates via dense/lexical/symbol channels"},
    "rerank":           {"id": "rerank",           "label": "Reranking",              "group": "ml", "dispatch_capable": False, "description": "Re-ranks retrieval candidates with token-overlap boost"},
    "query_rewrite":    {"id": "query_rewrite",    "label": "Query-Optimierung",      "group": "ml", "dispatch_capable": False, "description": "Rewrites query for better retrieval quality"},
    "cluster":          {"id": "cluster",          "label": "Graph-Clustering",       "group": "ml", "dispatch_capable": False, "description": "Clusters graph nodes (Leiden/Louvain/KMeans)"},
    "evolve_prompt":    {"id": "evolve_prompt",    "label": "Prompt Evolver",         "group": "ml", "dispatch_capable": False, "description": "Optimizes prompt templates via PlanningPromptEvolverService"},
    "evolve_project":   {"id": "evolve_project",   "label": "Projekt-Evolver",        "group": "ml", "dispatch_capable": False, "description": "Evolves project structure via EvolutionService"},
}

_GROUP_ORDER = {"control_flow": 0, "worker": 1, "ml": 2}


def list_task_kinds() -> list[TaskKindInfo]:
    """Return all task kinds ordered: control_flow → worker → ml."""
    return sorted(_KIND_INFO.values(), key=lambda k: (_GROUP_ORDER.get(k["group"], 9), k["id"]))


def get_task_kind_info(kind: str) -> TaskKindInfo | None:
    return _KIND_INFO.get(kind)


def is_legacy_kind(kind: str) -> bool:
    return kind in LEGACY_MAP


def suggested_replacement(kind: str) -> str | None:
    return LEGACY_MAP.get(kind)


def is_dispatch_capable(kind: str) -> bool:
    info = _KIND_INFO.get(kind)
    return info["dispatch_capable"] if info else False
