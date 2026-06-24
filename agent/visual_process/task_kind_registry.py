"""Canonical task kind registry (VPWRK-001).

Single source of truth for all valid task_kinds in the Visual Process Designer.
Groups: control_flow → worker → retrieval → ml
"""
from __future__ import annotations

from typing import TypedDict


WORKER_TASK_KINDS: frozenset[str] = frozenset({
    "patch_apply", "patch_propose", "command_execute", "shell_execute",
    "shell_execution", "plan_only", "review", "summarize", "research_limited",
    "run_tests", "script", "git_op", "file_check", "regex_check",
    "fork", "join", "approval",
    # Workspace diff/snapshot (WorkspaceDiffService — fully implemented)
    "workspace_snapshot", "workspace_diff",
})

# ML kinds: LLM-based or embedding/retrieval pipeline steps
ML_TASK_KINDS: frozenset[str] = frozenset({
    # Embedding (HTTP API or local hash — no local PyTorch in production)
    "embed_api",        # OpenAICompatibleEmbeddingProvider / HashEmbeddingProvider
    "embed_chunk",      # index_builder chunking + embed_api per chunk
    # TurboQuant (TQ-011 DeterministicSignRotation + TQ-012 4-bit PoC — TQ-013 ProdStub = NotImplementedError)
    "turboquant_mse",   # TurboQuantMseEncoder (TQ-012): sign-rotate + 4bit scalar quant — works
    "sign_rotation",    # DeterministicSignRotation (TQ-011) only — self-inverse, SHA256-based
    # RAG pipeline
    "rag_retrieve",     # HybridRetrievalService (6 channels: dense/lexical/symbol/cc_fts/cc_vec/cc_graph)
    "rerank",           # Reranker: token-overlap boost, weight=0.15 default
    "query_rewrite",    # Synonym expansion (bug→defect, fix→repair…) — NOT LLM rewriting
    # Evolution (EvolutionService — fully implemented: analyze/validate/apply)
    "evolution_analyze",    # EvolutionService.analyze(): context → proposals
    "evolution_validate",   # EvolutionService.validate(): proposal → validation
    "evolution_apply",      # EvolutionService.apply(): apply proposal (requires gate)
    # Prompt evolution (PlanningPromptEvolverService — fully implemented)
    "evolve_prompt",    # PlanningPromptEvolverService.evolve_from_run()
    "evolve_project",   # EvolutionService analyze+apply on project level
    # Domain clustering (deterministisch nach Pfad/Paket/Graph-Signalen — kein Leiden/Louvain)
    "domain_cluster",   # rag-helper domain_discovery.clustering — signal-based, deterministic
})

# CodeCompass steps (fully implemented: 19 modules in worker/retrieval/codecompass_*.py)
RETRIEVAL_TASK_KINDS: frozenset[str] = frozenset({
    "codecompass_index_build",    # index_builder: delta-detect → chunk → embed → store
    "codecompass_vector_search",  # codecompass_vector_engine: semantic + task/intent weights
    "codecompass_fts_search",     # codecompass_fts_engine: BM25 + task/intent weights
    "codecompass_graph_expand",   # codecompass_graph_expansion: neighbor expansion from seeds
})

# Legacy VP-Editor kinds → canonical Worker kinds
LEGACY_MAP: dict[str, str] = {
    "coding":           "patch_propose",
    "analysis":         "review",
    "code_review":      "review",
    "llm_generate":     "summarize",
    "deploy":           "shell_execute",
    "research":         "research_limited",
    "refactor":         "patch_apply",
    "goal_plan":        "plan_only",
    "goal_propose":     "plan_only",
    "bugfix":           "patch_apply",
    "testing":          "run_tests",
    "write_tests":      "run_tests",
    "read_file":        "file_check",
    "grep_search":      "script",
    "git_status":       "git_op",
    "git_diff":         "git_op",
    "parallel":         "fork",
    "infra":            "shell_execute",
    "ci":               "run_tests",
    "list_files":       "file_check",
    # Old unified kinds replaced by split kinds
    "vector_encode":    "embed_api",
    "turboquant_encode": "turboquant_mse",
    "cluster":          "domain_cluster",
}

ALL_TASK_KINDS: frozenset[str] = WORKER_TASK_KINDS | ML_TASK_KINDS | RETRIEVAL_TASK_KINDS

_CONTROL_FLOW_KINDS: frozenset[str] = frozenset({"fork", "join", "approval"})


class TaskKindInfo(TypedDict):
    id: str
    label: str
    group: str          # "control_flow" | "worker" | "retrieval" | "ml"
    dispatch_capable: bool
    description: str


_KIND_INFO: dict[str, TaskKindInfo] = {
    # ── Control flow ───────────────────────────────────────────────────────────
    "fork":      {"id": "fork",      "label": "Fork (Parallel)",     "group": "control_flow", "dispatch_capable": True,  "description": "Teilt Ausführung in parallele Zweige auf"},
    "join":      {"id": "join",      "label": "Join (Sync)",         "group": "control_flow", "dispatch_capable": True,  "description": "Wartet auf alle parallelen Zweige"},
    "approval":  {"id": "approval",  "label": "Approval Gate",       "group": "control_flow", "dispatch_capable": True,  "description": "Pausiert Workflow für menschliche Freigabe"},

    # ── Worker – mutation ──────────────────────────────────────────────────────
    "patch_apply":     {"id": "patch_apply",    "label": "Patch Anwenden",     "group": "worker", "dispatch_capable": True, "description": "Wendet Code-Patch auf Workspace an"},
    "patch_propose":   {"id": "patch_propose",  "label": "Patch Vorschlagen",  "group": "worker", "dispatch_capable": True, "description": "LLM erstellt Code-Patch-Vorschlag"},
    "command_execute": {"id": "command_execute","label": "Befehl Ausführen",   "group": "worker", "dispatch_capable": True, "description": "Führt deterministischen Befehl aus"},
    "shell_execute":   {"id": "shell_execute",  "label": "Shell Ausführen",    "group": "worker", "dispatch_capable": True, "description": "Führt Shell-Befehl aus (mit Sicherheitsfilter)"},

    # ── Worker – LLM readonly ──────────────────────────────────────────────────
    "plan_only":        {"id": "plan_only",        "label": "Planen (LLM)",        "group": "worker", "dispatch_capable": True, "description": "LLM-Planungsschritt, keine Mutationen"},
    "review":           {"id": "review",           "label": "Review (LLM)",         "group": "worker", "dispatch_capable": True, "description": "LLM Code- oder Dokumenten-Review"},
    "summarize":        {"id": "summarize",        "label": "Zusammenfassen (LLM)", "group": "worker", "dispatch_capable": True, "description": "LLM Text-Zusammenfassung"},
    "research_limited": {"id": "research_limited", "label": "Recherche (begrenzt)", "group": "worker", "dispatch_capable": True, "description": "Begrenzte Recherche ohne Netzwerk-Egress"},

    # ── Worker – deterministic ─────────────────────────────────────────────────
    "run_tests":   {"id": "run_tests",   "label": "Tests Ausführen", "group": "worker", "dispatch_capable": True, "description": "Führt Projekt-Test-Suite aus"},
    "script":      {"id": "script",      "label": "Script",          "group": "worker", "dispatch_capable": True, "description": "Führt deterministisches Script aus"},
    "git_op":      {"id": "git_op",      "label": "Git Operation",   "group": "worker", "dispatch_capable": True, "description": "Führt Git-Operation durch"},
    "file_check":  {"id": "file_check",  "label": "Datei Prüfen",    "group": "worker", "dispatch_capable": True, "description": "Prüft Datei-Existenz oder -Inhalt"},
    "regex_check": {"id": "regex_check", "label": "Regex Prüfen",    "group": "worker", "dispatch_capable": True, "description": "Regex-basierte Datei-Inhalts-Prüfung"},

    # ── Worker – workspace diff (WorkspaceDiffService — vollständig implementiert) ────
    "workspace_snapshot": {
        "id": "workspace_snapshot", "label": "Workspace Snapshot",
        "group": "worker", "dispatch_capable": True,
        "description": "Erstellt Hash-Map aller Workspace-Dateien via WorkspaceDiffService.take_snapshot()",
    },
    "workspace_diff": {
        "id": "workspace_diff", "label": "Workspace Diff",
        "group": "worker", "dispatch_capable": True,
        "description": "Berechnet Diff zwischen zwei Snapshots und erzeugt artifact_manifest.v1 (WorkspaceDiffService)",
    },

    # ── Retrieval / CodeCompass (19 Module — vollständig implementiert) ────────
    "codecompass_index_build": {
        "id": "codecompass_index_build", "label": "CC: Index aufbauen",
        "group": "retrieval", "dispatch_capable": False,
        "description": "Delta-Erkennung → Chunking → Embedding → SQLite-Index speichern (index_builder.py)",
    },
    "codecompass_vector_search": {
        "id": "codecompass_vector_search", "label": "CC: Semantic Search",
        "group": "retrieval", "dispatch_capable": False,
        "description": "Semantische Vektorsuche mit task_kind/intent-Gewichtung (codecompass_vector_engine)",
    },
    "codecompass_fts_search": {
        "id": "codecompass_fts_search", "label": "CC: Full-Text Search",
        "group": "retrieval", "dispatch_capable": False,
        "description": "BM25 Full-Text-Suche mit task_kind/intent-Gewichtung (codecompass_fts_engine)",
    },
    "codecompass_graph_expand": {
        "id": "codecompass_graph_expand", "label": "CC: Graph-Expansion",
        "group": "retrieval", "dispatch_capable": False,
        "description": "Graph-Nachbarschafts-Expansion von Seed-Knoten (codecompass_graph_expansion)",
    },

    # ── ML – Embedding ─────────────────────────────────────────────────────────
    "embed_api": {
        "id": "embed_api", "label": "Embedding (API)",
        "group": "ml", "dispatch_capable": False,
        "description": "Text → Embedding via OpenAICompatibleEmbeddingProvider (HTTP API), HashEmbeddingProvider oder FakeProvider. KEIN lokaler PyTorch/Transformer im Production-Code.",
    },
    "embed_chunk": {
        "id": "embed_chunk", "label": "Chunk + Einbetten",
        "group": "ml", "dispatch_capable": False,
        "description": "Chunked Dokumente und bettet jeden Chunk via embed_api ein (index_builder._build_entries_for_paths)",
    },
    "sign_rotation": {
        "id": "sign_rotation", "label": "Sign-Rotation (TQ-011)",
        "group": "ml", "dispatch_capable": False,
        "description": "DeterministicSignRotation: SHA256-basierter Per-Dimension Vorzeichenflip. Selbst-invers. Verwendet als Vorstufe vor Quantisierung.",
    },
    "turboquant_mse": {
        "id": "turboquant_mse", "label": "TurboQuant MSE (TQ-012 PoC)",
        "group": "ml", "dispatch_capable": False,
        "description": "4-bit Vektorkomprimierung: sign-rotate + symmetric scalar quant (TurboQuantMseEncoder). "
                       "PoC, experimentell. TQ-013 ProdStub = NotImplementedError (nicht verwendbar).",
    },

    # ── ML – RAG ───────────────────────────────────────────────────────────────
    "rag_retrieve": {
        "id": "rag_retrieve", "label": "RAG Abruf",
        "group": "ml", "dispatch_capable": False,
        "description": "HybridRetrievalService: 6 Channels (dense, lexical, symbol, codecompass_fts, codecompass_vector, codecompass_graph)",
    },
    "rerank": {
        "id": "rerank", "label": "Reranking",
        "group": "ml", "dispatch_capable": False,
        "description": "Reranker: Token-Overlap Boost auf Retrieval-Kandidaten (weight=0.15 default). NICHT neural.",
    },
    "query_rewrite": {
        "id": "query_rewrite", "label": "Query-Erweiterung",
        "group": "ml", "dispatch_capable": False,
        "description": "Synonym-Expansion (bug→defect/failure/issue, fix→repair/resolve…). Kein LLM-Rewriting — rein regelbasiert.",
    },

    # ── ML – Evolution (EvolutionService — vollständig implementiert) ──────────
    "evolution_analyze": {
        "id": "evolution_analyze", "label": "Evolution: Analysieren",
        "group": "ml", "dispatch_capable": False,
        "description": "EvolutionService.analyze(): Kontext → Evolution-Proposals. Persistiert EvolutionRunDB + EvolutionProposalDB.",
    },
    "evolution_validate": {
        "id": "evolution_validate", "label": "Evolution: Validieren",
        "group": "ml", "dispatch_capable": False,
        "description": "EvolutionService.validate(): Prüft Proposal ohne Anwendung (dry validation pass).",
    },
    "evolution_apply": {
        "id": "evolution_apply", "label": "Evolution: Anwenden",
        "group": "ml", "dispatch_capable": False,
        "description": "EvolutionService.apply(): Wendet validiertes Proposal an (über MutationGateService). Erfordert gate=True.",
    },

    # ── ML – Prompt / Project Evolution ───────────────────────────────────────
    "evolve_prompt": {
        "id": "evolve_prompt", "label": "Prompt Evolver",
        "group": "ml", "dispatch_capable": False,
        "description": "PlanningPromptEvolverService.evolve_from_run(): Optimiert User/Repair-Prompt-Templates anhand von Fehler-Triggern.",
    },
    "evolve_project": {
        "id": "evolve_project", "label": "Projekt-Evolver",
        "group": "ml", "dispatch_capable": False,
        "description": "EvolutionService Komplett-Pipeline auf Projekt-Ebene (analyze+apply). Höchstes Risiko.",
    },

    # ── ML – Clustering ────────────────────────────────────────────────────────
    "domain_cluster": {
        "id": "domain_cluster", "label": "Domain-Clustering",
        "group": "ml", "dispatch_capable": False,
        "description": "Deterministisches Signal-Clustering (Pfad/Paket/Graph-Kohäsion, rag-helper). "
                       "Leiden/Louvain/KMeans existieren NICHT im Production-Code.",
    },
}

_GROUP_ORDER = {"control_flow": 0, "worker": 1, "retrieval": 2, "ml": 3}


def list_task_kinds() -> list[TaskKindInfo]:
    """Return all task kinds ordered: control_flow → worker → retrieval → ml."""
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
