"""Canonical task kind registry (VPWRK-001 + VPRT-001/002).

Single source of truth for all valid task_kinds in the Visual Process Designer.
Groups: control_flow → worker → retrieval → ml

Runtime-Truth fields (VPRT-001):
  implementation_status  — production|experimental|stub|test_only|design_only|unknown
  implementation_state   — wired_and_executable|registered_only|implemented_not_exposed|
                           exposed_not_wired|legacy_alias|not_implemented
  backend_service        — canonical implementing class/module
  deterministic          — same input always produces same output
  uses_llm               — calls LLM API
  uses_network           — makes outbound network calls
  side_effects           — list from: none|read_workspace|write_manifest|write_index|
                           write_database|write_files|apply_patch|shell_execution|network_egress
  risk_level             — none|low|medium|high|critical
  legacy_aliases         — old kind names that map to this canonical kind
  requires_approval      — always requires gate/approval regardless of graph config
"""
from __future__ import annotations

from typing import TypedDict


WORKER_TASK_KINDS: frozenset[str] = frozenset({
    "patch_apply", "patch_propose", "command_execute", "shell_execute",
    "shell_execution", "plan_only", "review", "summarize", "research_limited",
    "run_tests", "script", "git_op", "file_check", "regex_check",
    "fork", "join", "approval",
    "workspace_snapshot", "workspace_diff",
})

ML_TASK_KINDS: frozenset[str] = frozenset({
    "embed_api", "embed_chunk",
    "turboquant_mse", "sign_rotation",
    "rag_retrieve", "rerank", "query_rewrite",
    "evolution_analyze", "evolution_validate", "evolution_apply",
    "evolve_prompt", "evolve_project",
    "domain_cluster",
})

RETRIEVAL_TASK_KINDS: frozenset[str] = frozenset({
    "codecompass_index_build",
    "codecompass_vector_search",
    "codecompass_fts_search",
    "codecompass_graph_expand",
})

LEGACY_MAP: dict[str, str] = {
    "coding":            "patch_propose",
    "analysis":          "review",
    "code_review":       "review",
    "llm_generate":      "summarize",
    "deploy":            "shell_execute",
    "research":          "research_limited",
    "refactor":          "patch_apply",
    "goal_plan":         "plan_only",
    "goal_propose":      "plan_only",
    "bugfix":            "patch_apply",
    "testing":           "run_tests",
    "write_tests":       "run_tests",
    "read_file":         "file_check",
    "grep_search":       "script",
    "git_status":        "git_op",
    "git_diff":          "git_op",
    "parallel":          "fork",
    "infra":             "shell_execute",
    "ci":                "run_tests",
    "list_files":        "file_check",
    "vector_encode":     "embed_api",
    "turboquant_encode": "turboquant_mse",
    "cluster":           "domain_cluster",
}

ALL_TASK_KINDS: frozenset[str] = WORKER_TASK_KINDS | ML_TASK_KINDS | RETRIEVAL_TASK_KINDS

_CONTROL_FLOW_KINDS: frozenset[str] = frozenset({"fork", "join", "approval"})


class TaskKindInfo(TypedDict):
    id: str
    label: str
    group: str
    dispatch_capable: bool
    description: str
    # Runtime-Truth (VPRT-001)
    implementation_status: str
    implementation_state: str
    backend_service: str
    deterministic: bool
    uses_llm: bool
    uses_network: bool
    side_effects: list
    risk_level: str
    legacy_aliases: list
    requires_approval: bool


_KIND_INFO: dict[str, TaskKindInfo] = {
    # ── Control flow ───────────────────────────────────────────────────────────
    "fork": {
        "id": "fork", "label": "Fork (Parallel)", "group": "control_flow", "dispatch_capable": True,
        "description": "Teilt Ausführung in parallele Zweige auf",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "WorkflowExecutor",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "low", "legacy_aliases": ["parallel"], "requires_approval": False,
    },
    "join": {
        "id": "join", "label": "Join (Sync)", "group": "control_flow", "dispatch_capable": True,
        "description": "Wartet auf alle parallelen Zweige",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "WorkflowExecutor",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "low", "legacy_aliases": [], "requires_approval": False,
    },
    "approval": {
        "id": "approval", "label": "Approval Gate", "group": "control_flow", "dispatch_capable": True,
        "description": "Pausiert Workflow für menschliche Freigabe",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "MutationGateService",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "low", "legacy_aliases": [], "requires_approval": True,
    },

    # ── Worker – mutation ──────────────────────────────────────────────────────
    "patch_apply": {
        "id": "patch_apply", "label": "Patch Anwenden", "group": "worker", "dispatch_capable": True,
        "description": "Wendet Code-Patch auf Workspace an",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "PatchApplyWorker",
        "deterministic": False, "uses_llm": False, "uses_network": False,
        "side_effects": ["apply_patch", "write_files"], "risk_level": "medium",
        "legacy_aliases": ["refactor", "bugfix"], "requires_approval": False,
    },
    "patch_propose": {
        "id": "patch_propose", "label": "Patch Vorschlagen", "group": "worker", "dispatch_capable": True,
        "description": "LLM erstellt Code-Patch-Vorschlag",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "PatchProposeWorker",
        "deterministic": False, "uses_llm": True, "uses_network": False,
        "side_effects": [], "risk_level": "low",
        "legacy_aliases": ["coding"], "requires_approval": False,
    },
    "command_execute": {
        "id": "command_execute", "label": "Befehl Ausführen", "group": "worker", "dispatch_capable": True,
        "description": "Führt deterministischen Befehl aus",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "CommandExecuteWorker",
        "deterministic": False, "uses_llm": False, "uses_network": False,
        "side_effects": ["shell_execution"], "risk_level": "high",
        "legacy_aliases": [], "requires_approval": False,
    },
    "shell_execute": {
        "id": "shell_execute", "label": "Shell Ausführen", "group": "worker", "dispatch_capable": True,
        "description": "Führt Shell-Befehl aus (mit Sicherheitsfilter)",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "ShellExecuteWorker",
        "deterministic": False, "uses_llm": False, "uses_network": False,
        "side_effects": ["shell_execution"], "risk_level": "high",
        "legacy_aliases": ["deploy", "infra", "shell_execution"], "requires_approval": False,
    },

    # ── Worker – LLM readonly ──────────────────────────────────────────────────
    "plan_only": {
        "id": "plan_only", "label": "Planen (LLM)", "group": "worker", "dispatch_capable": True,
        "description": "LLM-Planungsschritt, keine Mutationen",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "PlanOnlyWorker",
        "deterministic": False, "uses_llm": True, "uses_network": False,
        "side_effects": [], "risk_level": "low",
        "legacy_aliases": ["goal_plan", "goal_propose"], "requires_approval": False,
    },
    "review": {
        "id": "review", "label": "Review (LLM)", "group": "worker", "dispatch_capable": True,
        "description": "LLM Code- oder Dokumenten-Review",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "ReviewWorker",
        "deterministic": False, "uses_llm": True, "uses_network": False,
        "side_effects": [], "risk_level": "none",
        "legacy_aliases": ["analysis", "code_review"], "requires_approval": False,
    },
    "summarize": {
        "id": "summarize", "label": "Zusammenfassen (LLM)", "group": "worker", "dispatch_capable": True,
        "description": "LLM Text-Zusammenfassung",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "SummarizeWorker",
        "deterministic": False, "uses_llm": True, "uses_network": False,
        "side_effects": [], "risk_level": "none",
        "legacy_aliases": ["llm_generate"], "requires_approval": False,
    },
    "research_limited": {
        "id": "research_limited", "label": "Recherche (begrenzt)", "group": "worker", "dispatch_capable": True,
        "description": "Begrenzte Recherche ohne Netzwerk-Egress",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "ResearchWorker",
        "deterministic": False, "uses_llm": True, "uses_network": False,
        "side_effects": [], "risk_level": "low",
        "legacy_aliases": ["research"], "requires_approval": False,
    },

    # ── Worker – deterministic ─────────────────────────────────────────────────
    "run_tests": {
        "id": "run_tests", "label": "Tests Ausführen", "group": "worker", "dispatch_capable": True,
        "description": "Führt Projekt-Test-Suite aus",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "RunTestsWorker",
        "deterministic": False, "uses_llm": False, "uses_network": False,
        "side_effects": ["shell_execution"], "risk_level": "low",
        "legacy_aliases": ["testing", "write_tests", "ci"], "requires_approval": False,
    },
    "script": {
        "id": "script", "label": "Script", "group": "worker", "dispatch_capable": True,
        "description": "Führt deterministisches Script aus",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "ScriptWorker",
        "deterministic": False, "uses_llm": False, "uses_network": False,
        "side_effects": ["shell_execution"], "risk_level": "medium",
        "legacy_aliases": ["grep_search"], "requires_approval": False,
    },
    "git_op": {
        "id": "git_op", "label": "Git Operation", "group": "worker", "dispatch_capable": True,
        "description": "Führt Git-Operation durch",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "GitOpWorker",
        "deterministic": False, "uses_llm": False, "uses_network": False,
        "side_effects": ["write_files"], "risk_level": "medium",
        "legacy_aliases": ["git_status", "git_diff"], "requires_approval": False,
    },
    "file_check": {
        "id": "file_check", "label": "Datei Prüfen", "group": "worker", "dispatch_capable": True,
        "description": "Prüft Datei-Existenz oder -Inhalt",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "FileCheckWorker",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": ["read_workspace"], "risk_level": "none",
        "legacy_aliases": ["read_file", "list_files"], "requires_approval": False,
    },
    "regex_check": {
        "id": "regex_check", "label": "Regex Prüfen", "group": "worker", "dispatch_capable": True,
        "description": "Regex-basierte Datei-Inhalts-Prüfung",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "RegexCheckWorker",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": ["read_workspace"], "risk_level": "none",
        "legacy_aliases": [], "requires_approval": False,
    },

    # ── Worker – workspace diff (WorkspaceDiffService) ────────────────────────
    "workspace_snapshot": {
        "id": "workspace_snapshot", "label": "Workspace Snapshot", "group": "worker", "dispatch_capable": True,
        "description": "Erstellt Hash-Map aller Workspace-Dateien via WorkspaceDiffService.take_before_snapshot()",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "WorkspaceDiffService.take_before_snapshot",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": ["read_workspace"], "risk_level": "none",
        "legacy_aliases": [], "requires_approval": False,
    },
    "workspace_diff": {
        "id": "workspace_diff", "label": "Workspace Diff", "group": "worker", "dispatch_capable": True,
        "description": "Berechnet Diff zwischen zwei Snapshots und erzeugt artifact_manifest.v1 (WorkspaceDiffService)",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "WorkspaceDiffService.compute_diff + synthesize_manifest",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": ["read_workspace", "write_manifest"], "risk_level": "low",
        "legacy_aliases": [], "requires_approval": False,
    },

    # ── Retrieval / CodeCompass (19 Module — vollständig implementiert) ────────
    "codecompass_index_build": {
        "id": "codecompass_index_build", "label": "CC: Index aufbauen", "group": "retrieval", "dispatch_capable": False,
        "description": "Delta-Erkennung → Chunking → Embedding → SQLite-Index speichern (index_builder.py)",
        "implementation_status": "production", "implementation_state": "registered_only",
        "backend_service": "CodeCompassIndexBuilder",
        "deterministic": False, "uses_llm": False, "uses_network": False,
        "side_effects": ["write_index"], "risk_level": "low",
        "legacy_aliases": [], "requires_approval": False,
    },
    "codecompass_vector_search": {
        "id": "codecompass_vector_search", "label": "CC: Semantic Search", "group": "retrieval", "dispatch_capable": False,
        "description": "Semantische Vektorsuche mit task_kind/intent-Gewichtung (codecompass_vector_engine)",
        "implementation_status": "production", "implementation_state": "registered_only",
        "backend_service": "CodeCompassVectorEngine",
        "deterministic": False, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "none",
        "legacy_aliases": [], "requires_approval": False,
    },
    "codecompass_fts_search": {
        "id": "codecompass_fts_search", "label": "CC: Full-Text Search", "group": "retrieval", "dispatch_capable": False,
        "description": "BM25 Full-Text-Suche mit task_kind/intent-Gewichtung (codecompass_fts_engine)",
        "implementation_status": "production", "implementation_state": "registered_only",
        "backend_service": "CodeCompassFtsEngine",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "none",
        "legacy_aliases": [], "requires_approval": False,
    },
    "codecompass_graph_expand": {
        "id": "codecompass_graph_expand", "label": "CC: Graph-Expansion", "group": "retrieval", "dispatch_capable": False,
        "description": "Graph-Nachbarschafts-Expansion von Seed-Knoten (codecompass_graph_expansion)",
        "implementation_status": "production", "implementation_state": "registered_only",
        "backend_service": "CodeCompassGraphExpansion",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "none",
        "legacy_aliases": [], "requires_approval": False,
    },

    # ── ML – Embedding ─────────────────────────────────────────────────────────
    "embed_api": {
        "id": "embed_api", "label": "Embedding API", "group": "ml", "dispatch_capable": False,
        "description": (
            "Text → Embedding via OpenAICompatibleEmbeddingProvider (HTTP API), "
            "HashEmbeddingProvider oder FakeProvider. KEIN lokaler PyTorch/Transformer im Production-Code."
        ),
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "OpenAICompatibleEmbeddingProvider / HashEmbeddingProvider",
        "deterministic": False, "uses_llm": False, "uses_network": True,
        "side_effects": ["network_egress"], "risk_level": "none",
        "legacy_aliases": ["vector_encode"], "requires_approval": False,
    },
    "embed_chunk": {
        "id": "embed_chunk", "label": "Chunk + Einbetten", "group": "ml", "dispatch_capable": False,
        "description": "Chunked Dokumente und bettet jeden Chunk via embed_api ein (index_builder._build_entries_for_paths)",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "CodeCompassIndexBuilder._build_entries_for_paths",
        "deterministic": False, "uses_llm": False, "uses_network": True,
        "side_effects": ["read_workspace", "network_egress"], "risk_level": "none",
        "legacy_aliases": [], "requires_approval": False,
    },

    # ── ML – TurboQuant ────────────────────────────────────────────────────────
    "sign_rotation": {
        "id": "sign_rotation", "label": "Sign-Rotation (TQ-011)", "group": "ml", "dispatch_capable": False,
        "description": "DeterministicSignRotation: SHA256-basierter Per-Dimension Vorzeichenflip. Selbst-invers. Production.",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "DeterministicSignRotation",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "none",
        "legacy_aliases": [], "requires_approval": False,
    },
    "turboquant_mse": {
        "id": "turboquant_mse", "label": "TurboQuant MSE (experimentell)", "group": "ml", "dispatch_capable": False,
        "description": (
            "Funktionierender experimenteller 4-bit Encoder (TQ-012 TurboQuantMseEncoder): "
            "DeterministicSignRotation + symmetric scalar quant + Decode. "
            "Kein Produktions-Codebook (TQ-013 ProdStub = separater, nicht verwendbarer Stub)."
        ),
        "implementation_status": "experimental", "implementation_state": "wired_and_executable",
        "backend_service": "TurboQuantMseEncoder",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "none",
        "legacy_aliases": ["turboquant_encode"], "requires_approval": False,
    },

    # ── ML – RAG ───────────────────────────────────────────────────────────────
    "rag_retrieve": {
        "id": "rag_retrieve", "label": "RAG Abruf", "group": "ml", "dispatch_capable": False,
        "description": "HybridRetrievalService: 6 Channels (dense, lexical, symbol, codecompass_fts, codecompass_vector, codecompass_graph)",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "HybridRetrievalService",
        "deterministic": False, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "none",
        "legacy_aliases": [], "requires_approval": False,
    },
    "rerank": {
        "id": "rerank", "label": "Reranking", "group": "ml", "dispatch_capable": False,
        "description": "Reranker: Token-Overlap Boost auf Retrieval-Kandidaten (weight=0.15 default). NICHT neural.",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "Reranker",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "none",
        "legacy_aliases": [], "requires_approval": False,
    },
    "query_rewrite": {
        "id": "query_rewrite", "label": "Query-Erweiterung", "group": "ml", "dispatch_capable": False,
        "description": "Synonym-Expansion (bug→defect/failure/issue, fix→repair/resolve…). Kein LLM — rein regelbasiert.",
        "implementation_status": "production", "implementation_state": "wired_and_executable",
        "backend_service": "rewrite_query (worker/retrieval/query_rewrite.py)",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "none",
        "legacy_aliases": [], "requires_approval": False,
    },

    # ── ML – Evolution (EvolutionService) ─────────────────────────────────────
    "evolution_analyze": {
        "id": "evolution_analyze", "label": "Evolution: Analysieren", "group": "ml", "dispatch_capable": False,
        "description": "EvolutionService.analyze(): Kontext → Evolution-Proposals. Persistiert EvolutionRunDB + EvolutionProposalDB.",
        "implementation_status": "production", "implementation_state": "registered_only",
        "backend_service": "EvolutionService.analyze",
        "deterministic": False, "uses_llm": True, "uses_network": False,
        "side_effects": ["write_database"], "risk_level": "medium",
        "legacy_aliases": [], "requires_approval": False,
    },
    "evolution_validate": {
        "id": "evolution_validate", "label": "Evolution: Validieren", "group": "ml", "dispatch_capable": False,
        "description": "EvolutionService.validate(): Prüft Proposal ohne Anwendung (dry validation pass).",
        "implementation_status": "production", "implementation_state": "registered_only",
        "backend_service": "EvolutionService.validate",
        "deterministic": False, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "low",
        "legacy_aliases": [], "requires_approval": False,
    },
    "evolution_apply": {
        "id": "evolution_apply", "label": "Evolution: Anwenden", "group": "ml", "dispatch_capable": False,
        "description": "EvolutionService.apply(): Wendet validiertes Proposal an (via MutationGateService). Erfordert gate=True.",
        "implementation_status": "production", "implementation_state": "registered_only",
        "backend_service": "EvolutionService.apply",
        "deterministic": False, "uses_llm": True, "uses_network": False,
        "side_effects": ["write_files", "write_database"], "risk_level": "high",
        "legacy_aliases": [], "requires_approval": True,
    },
    "evolve_prompt": {
        "id": "evolve_prompt", "label": "Prompt Evolver", "group": "ml", "dispatch_capable": False,
        "description": "PlanningPromptEvolverService.evolve_from_run(): Optimiert User/Repair-Prompt-Templates.",
        "implementation_status": "production", "implementation_state": "registered_only",
        "backend_service": "PlanningPromptEvolverService",
        "deterministic": False, "uses_llm": True, "uses_network": False,
        "side_effects": ["write_database"], "risk_level": "medium",
        "legacy_aliases": [], "requires_approval": False,
    },
    "evolve_project": {
        "id": "evolve_project", "label": "Projekt-Evolver", "group": "ml", "dispatch_capable": False,
        "description": "EvolutionService Komplett-Pipeline auf Projekt-Ebene (analyze+apply). Höchstes Risiko.",
        "implementation_status": "production", "implementation_state": "registered_only",
        "backend_service": "EvolutionService (full pipeline)",
        "deterministic": False, "uses_llm": True, "uses_network": False,
        "side_effects": ["write_files", "write_database"], "risk_level": "critical",
        "legacy_aliases": [], "requires_approval": True,
    },

    # ── ML – Clustering ────────────────────────────────────────────────────────
    "domain_cluster": {
        "id": "domain_cluster", "label": "Domain-Clustering", "group": "ml", "dispatch_capable": False,
        "description": (
            "Deterministisches Signal-Clustering (Pfad/Paket/Graph-Kohäsion, rag-helper). "
            "Leiden/Louvain/KMeans existieren NICHT im Production-Code."
        ),
        "implementation_status": "production", "implementation_state": "registered_only",
        "backend_service": "rag-helper domain_discovery.clustering",
        "deterministic": True, "uses_llm": False, "uses_network": False,
        "side_effects": [], "risk_level": "none",
        "legacy_aliases": ["cluster"], "requires_approval": False,
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
