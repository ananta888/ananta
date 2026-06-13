"""CRPS-002/003/004/009: RetrievalProfileResolver — intent classification and profile resolution."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ──────────────────────────────────────────
# Domain and Intent constants
# ──────────────────────────────────────────

DOMAIN_CODECOMPASS = "codecompass"
DOMAIN_AI_SNAKE = "ai_snake"
DOMAIN_WORKER = "worker"
DOMAIN_ANANTA_GAME = "ananta_game"
DOMAIN_OPERATOR_TUI = "operator_tui"
DOMAIN_OPS = "ops"
DOMAIN_GENERIC = "generic"

INTENT_CODE_EXPLANATION = "implemented_code_explanation"
INTENT_ARCHITECTURE = "architecture_overview"
INTENT_DOCS = "docs_overview"
INTENT_TUTORIAL = "tutorial_help"
INTENT_GAME_DESIGN = "game_design"
INTENT_OPS_RUNBOOK = "ops_runbook"
INTENT_MERMAID = "mermaid_request"
INTENT_ARCHITECTURE_FULL_SCAN = "architecture_full_scan"
INTENT_GENERIC_CHAT = "generic_chat"

_VALID_SOURCE_TYPES: frozenset[str] = frozenset({"repo", "artifact", "wiki", "task_memory"})


# ──────────────────────────────────────────
# CRPS-002: RetrievalProfile dataclass
# ──────────────────────────────────────────

@dataclass
class RetrievalProfile:
    profile_id: str
    domain: str
    intent: str
    source_types: list[str] = field(default_factory=list)
    source_type_weights: dict[str, float] = field(default_factory=dict)
    retrieval_intent: str = ""
    negative_source_patterns: list[str] = field(default_factory=list)
    feature_flag: str = "auto"
    warnings: list[str] = field(default_factory=list)
    selected_by: str = "retrieval_profile_resolver.v1"
    reasons: list[str] = field(default_factory=list)
    source_policy: dict[str, Any] = field(default_factory=dict)
    chunk_policy: dict[str, Any] = field(default_factory=dict)
    expansion_policy: dict[str, Any] = field(default_factory=dict)
    explainability: dict[str, Any] = field(default_factory=dict)
    analysis_mode: str = ""
    output_intent: str = ""
    coverage_policy: str = ""
    summary_policy: str = ""
    budgets: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "domain": self.domain,
            "intent": self.intent,
            "source_types": list(self.source_types),
            "source_type_weights": dict(self.source_type_weights),
            "retrieval_intent": self.retrieval_intent,
            "negative_source_patterns": list(self.negative_source_patterns),
            "feature_flag": self.feature_flag,
            "warnings": list(self.warnings),
            "selected_by": self.selected_by,
            "reasons": list(self.reasons),
            "source_policy": dict(self.source_policy),
            "chunk_policy": dict(self.chunk_policy),
            "expansion_policy": dict(self.expansion_policy),
            "explainability": dict(self.explainability),
            "analysis_mode": self.analysis_mode,
            "output_intent": self.output_intent,
            "coverage_policy": self.coverage_policy,
            "summary_policy": self.summary_policy,
            "budgets": dict(self.budgets),
        }


def normalize_retrieval_profile(raw: dict[str, Any] | None) -> RetrievalProfile | None:
    """Parse a raw dict into a RetrievalProfile; returns None if unrecognized."""
    if not raw or not isinstance(raw, dict):
        return None
    profile_id = str(raw.get("profile_id") or "").strip()
    domain = str(raw.get("domain") or "").strip()
    intent = str(raw.get("intent") or "").strip()
    if not profile_id or not domain or not intent:
        return None

    warnings: list[str] = []
    raw_source_types = [str(st) for st in list(raw.get("source_types") or []) if str(st).strip()]
    source_types = [st for st in raw_source_types if st in _VALID_SOURCE_TYPES]
    for st in raw_source_types:
        if st not in _VALID_SOURCE_TYPES:
            warnings.append(f"unknown_source_type:{st}")

    source_type_weights: dict[str, float] = {}
    for k, v in dict(raw.get("source_type_weights") or {}).items():
        ks = str(k).strip()
        if not ks:
            continue
        try:
            source_type_weights[ks] = float(v)
        except (TypeError, ValueError):
            warnings.append(f"invalid_weight_for:{ks}")

    retrieval_intent = str(raw.get("retrieval_intent") or "").strip()
    negative_source_patterns = [str(p) for p in list(raw.get("negative_source_patterns") or []) if str(p).strip()]
    feature_flag = str(raw.get("feature_flag") or "auto").strip()
    selected_by = str(raw.get("selected_by") or "raw_profile").strip()
    reasons = [str(r) for r in list(raw.get("reasons") or []) if str(r).strip()]
    analysis_mode = str(raw.get("analysis_mode") or "").strip()
    output_intent = str(raw.get("output_intent") or "").strip()
    coverage_policy = str(raw.get("coverage_policy") or "").strip()
    summary_policy = str(raw.get("summary_policy") or "").strip()
    budgets = _normalize_budgets(raw.get("budgets"))
    return RetrievalProfile(
        profile_id=profile_id,
        domain=domain,
        intent=intent,
        source_types=source_types,
        source_type_weights=source_type_weights,
        retrieval_intent=retrieval_intent,
        negative_source_patterns=negative_source_patterns,
        feature_flag=feature_flag,
        warnings=warnings,
        selected_by=selected_by,
        reasons=reasons,
        source_policy=dict(raw.get("source_policy") or _build_source_policy(source_types, source_type_weights, negative_source_patterns)),
        chunk_policy=dict(raw.get("chunk_policy") or _default_chunk_policy()),
        expansion_policy=dict(raw.get("expansion_policy") or _default_expansion_policy()),
        explainability=dict(raw.get("explainability") or _default_explainability()),
        analysis_mode=analysis_mode,
        output_intent=output_intent,
        coverage_policy=coverage_policy,
        summary_policy=summary_policy,
        budgets=budgets,
    )


def _default_chunk_policy() -> dict[str, Any]:
    return {
        "preferred_granularity": ["symbol", "line_range", "snippet", "chunk", "file_excerpt"],
        "prefer_chunks_over_context_text": True,
        "max_chunks": 12,
        "max_per_source": 2,
        "max_per_source_type": {"repo": 8, "artifact": 4, "wiki": 1, "task_memory": 3},
    }


def _default_expansion_policy() -> dict[str, Any]:
    return {
        "graph_expansion": True,
        "relation_expansion": True,
        "source_neighbor_expansion": True,
    }


def _default_explainability() -> dict[str, Any]:
    return {
        "include_profile_id": True,
        "include_selected_by": True,
        "include_rejected_sources_summary": True,
    }


def _normalize_budgets(raw: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    if not isinstance(raw, dict):
        return result
    bounds = {
        "max_batches": (1, 64),
        "files_per_batch": (1, 20),
        "max_ref_chars": (500, 40_000),
        "max_total_ref_count": (1, 500),
        "max_summary_chars": (1_000, 80_000),
    }
    for key, (lo, hi) in bounds.items():
        value = raw.get(key)
        if value is None:
            continue
        try:
            result[key] = max(lo, min(hi, int(value)))
        except (TypeError, ValueError):
            continue
    return result


def _default_full_scan_budgets() -> dict[str, int]:
    return {
        "max_batches": 8,
        "files_per_batch": 3,
        "max_ref_chars": 4000,
        "max_total_ref_count": 24,
        "max_summary_chars": 12000,
    }


def _is_rag_iterative_intent(cfg: dict[str, Any]) -> bool:
    """Return True when the user explicitly selected the rag_iterative mode."""
    mode = str(cfg.get("chat_architecture_analysis_mode") or "").strip().lower()
    return mode == "rag_iterative"


def _is_full_scan_intent(query: str, intent: str, cfg: dict[str, Any]) -> bool:
    """Decide whether the active query should trigger the heavy architecture
    full_scan path.

    Decision order (highest priority first):

    1. Explicit user setting via ``chat_architecture_analysis_mode``:
         - ``full_scan`` / ``architecture_full_scan`` / ``force_full_scan``  → ON
         - ``off`` / ``disabled`` / ``quick`` / ``standard``               → OFF
       This is the contract: a user's explicit choice is always honored,
       regardless of keywords in the question. ``auto`` is treated as
       "user has not decided" and falls through to the heuristics below.
    2. Upstream classifier intent: if ``INTENT_ARCHITECTURE_FULL_SCAN`` was
       selected by ``classify_retrieval_intent`` (e.g. because the
       ``intent_override`` or a domain-specific profile set it), full_scan
       is on.
    3. Heuristic word triggers — only meaningful when the question is
       unambiguously asking for a broad multi-file analysis:

       a. Mermaid intent + explicit architecture-diagram phrasing
          (e.g. "mermaid architekturdiagramm"). ``codecompass`` is
          deliberately **excluded** — it is a domain name, not an intent
          signal, and would otherwise cause every CodeCompass question to
          escalate to the expensive full_scan path.
       b. Standalone full-scan keywords ("architekturdiagramm",
          "architecture diagram", "gesamtarchitektur", "vollanalyse",
          "full scan"). These are unambiguous "I want the whole picture"
          requests and may trigger full_scan even outside the mermaid
          intent.

    The legacy "architektur", "architecture", "gesamt", "worker handoff"
    markers were removed: they fired on incidental mentions of architecture
    in code questions and forced the user onto the slow path against their
    explicit ``auto`` / ``off`` preference.
    """
    mode = str(cfg.get("chat_architecture_analysis_mode") or cfg.get("analysis_mode") or "").strip().lower()
    if mode in {"architecture_full_scan", "full_scan", "force_full_scan"}:
        return True
    if mode in {"off", "disabled", "quick", "standard"}:
        return False
    q = str(query or "").lower()
    if intent == INTENT_ARCHITECTURE_FULL_SCAN:
        return True
    # Mermaid intent + unambiguous architecture-diagram phrasing. The
    # ``codecompass`` token was removed from this list because it is the
    # domain name (a noun users mention freely) and was hijacking every
    # CodeCompass-related question into the expensive full_scan path.
    if intent == INTENT_MERMAID and any(
        marker in q
        for marker in ("architekturdiagramm", "architecture diagram", "gesamtarchitektur")
    ):
        return True
    # Standalone full-scan keywords — explicit "give me the whole picture"
    # requests. These remain a valid trigger even outside the mermaid intent
    # because they are unambiguous.
    if any(marker in q for marker in ("architekturdiagramm", "architecture diagram", "gesamtarchitektur", "vollanalyse", "full scan")):
        return True
    return False


def _resolve_output_intent(query: str, intent: str) -> str:
    q = str(query or "").lower()
    if "sequence" in q or "sequenz" in q or "ablauf" in q or "handoff" in q:
        return "mermaid_sequence_diagram"
    if "mermaid" in q or "diagram" in q or "diagramm" in q:
        return "mermaid_component_diagram"
    if "dependency" in q or "abhängig" in q or "abhaengig" in q:
        return "dependency_map"
    if intent == INTENT_ARCHITECTURE_FULL_SCAN:
        return "architecture_overview"
    return ""


def _build_source_policy(
    source_types: list[str],
    source_type_weights: dict[str, float],
    negative_source_patterns: list[str],
) -> dict[str, Any]:
    weighted_order = sorted(
        ((source_type, float(weight)) for source_type, weight in source_type_weights.items()),
        key=lambda item: (-item[1], item[0]),
    )
    priority_order = [source_type for source_type, _ in weighted_order if source_type in _VALID_SOURCE_TYPES]
    for source_type in source_types:
        if source_type not in priority_order:
            priority_order.append(source_type)
    return {
        "requested_source_types": list(source_types),
        "priority_order": priority_order,
        "source_type_weights": dict(source_type_weights),
        "negative_source_patterns": list(negative_source_patterns),
        "required_min_source_type_counts": {"repo": 2} if "repo" in source_types else {},
    }


# ──────────────────────────────────────────
# CRPS-003: domain + intent keyword tables
# ──────────────────────────────────────────

_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    DOMAIN_CODECOMPASS: (
        "codecompass", "code compass", "snippet", "symbol", "line range",
        "line_range", "codeindex", "code index",
    ),
    DOMAIN_AI_SNAKE: (
        "ai-snake", "ai snake", "snakes.py", "snake_ask", "chatpanel",
        "chat panel",
    ),
    DOMAIN_WORKER: (
        "ananta-worker", "ananta worker", "worker", "sgpt", "iterative",
        "task_kind", "batches", "worker context", "worker handoff",
    ),
    DOMAIN_ANANTA_GAME: (
        "ananta game", "game", "spielstand", "spieler", "multiplayer",
        "tutorial mode", "tutorialmodus", "lore", "book of ananta",
        "book-of-ananta",
    ),
    DOMAIN_OPERATOR_TUI: (
        "tui", "terminal ui", "operator", "drag selection", "clipboard",
        "status line", "statuszeile",
    ),
    DOMAIN_OPS: (
        "deployment", "docker", "container", "ops", "prometheus", "metric",
        "monitoring", "runbook", "neustart", "restart",
    ),
}

_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    INTENT_ARCHITECTURE_FULL_SCAN: (
        "architekturdiagramm", "architecture diagram", "gesamtarchitektur",
        "full scan", "vollanalyse", "vollständige analyse", "vollstaendige analyse",
        "alle relevanten komponenten", "all relevant components",
        "mermaid diagramm zur architektur", "mermaid diagram for architecture",
        "dependency map", "abhängigkeitsdiagramm", "abhaengigkeitsdiagramm",
    ),
    INTENT_CODE_EXPLANATION: (
        "wie funktioniert", "how does", "explain", "erklär", "erkläre",
        "was macht", "what does", "zeig mir", "show me", "implementiert",
        "implemented", "code", "funktion", "function", "klasse", "class",
        "methode", "method", "modul", "module", "mechanismus", "mechanism",
        "datei", "file",
    ),
    INTENT_ARCHITECTURE: (
        "architektur", "architecture", "aufbau", "structure", "overview",
        "überblick", "ueberblick", "komponenten", "components", "design",
        "how is", "wie ist", "zusammenhang", "dependencies", "abhängigkeiten",
        "flow", "ablauf",
    ),
    INTENT_DOCS: (
        "docs", "documentation", "dokumentation", "readme", "guide",
        "anleitung", "runbook", "adr", "concept", "konzept",
    ),
    INTENT_TUTORIAL: (
        "tutorial", "lernen", "learn", "wie mache ich", "how do i",
        "how to", "schritt", "step", "anfänger", "beginner", "beispiel",
        "example",
    ),
    INTENT_GAME_DESIGN: (
        "spieldesign", "game design", "level", "spielmechanik", "mechanic",
        "spielregel", "rule", "punkte", "score", "herausforderung",
    ),
    INTENT_OPS_RUNBOOK: (
        "restart", "neustart", "deploy", "deployment", "monitor", "alert",
        "pager", "oncall", "on-call", "recover", "recovery",
        "konfiguriere", "configure", "setup",
    ),
    INTENT_MERMAID: (
        "mermaid", "diagram", "diagramm", "flowchart", "sequence",
        "sequenz", "classdiagram", "erdiagram",
    ),
}


def classify_retrieval_intent(
    query: str,
    ui_config: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Deterministic, no-LLM classifier.
    Returns (domain, intent) string tuple using constants from this module.
    """
    q = str(query or "").lower()
    cfg = dict(ui_config or {})

    domain = DOMAIN_GENERIC
    best_domain_hits = 0
    for dom, keywords in _DOMAIN_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in q)
        if hits > best_domain_hits:
            best_domain_hits = hits
            domain = dom

    intent = INTENT_GENERIC_CHAT
    best_intent_hits = 0
    for int_type, keywords in _INTENT_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in q)
        if hits > best_intent_hits:
            best_intent_hits = hits
            intent = int_type

    # UI config signals: chat_codecompass_trigger_mode (explicit user override)
    # Takes precedence over the keyword-based classification so the user can
    # force a specific domain regardless of question wording. Valid values:
    #   "auto"             — no override, fall through to keyword classification
    #   "force_codecompass" — domain locked to CODECOMPASS, intent preserved
    #   "force_repo_first"  — domain stays as classified, intent locked to
    #                         code_explanation with repo_first weights
    #   "disabled"          — domain/intent forced to generic_chat (no RAG)
    # See TUI choice "CodeCompass Trigger" in the Kontext/RAG group.
    _trigger_mode = str(cfg.get("chat_codecompass_trigger_mode") or "auto").strip().lower()
    if _trigger_mode == "disabled":
        domain = DOMAIN_GENERIC
        intent = INTENT_GENERIC_CHAT
    elif _trigger_mode == "force_codecompass":
        domain = DOMAIN_CODECOMPASS
        # Keep the classified intent unless we are still on generic — in that
        # case upgrade to code_explanation because the user explicitly asked
        # for CodeCompass-driven retrieval.
        if intent == INTENT_GENERIC_CHAT:
            intent = INTENT_CODE_EXPLANATION
    elif _trigger_mode == "force_repo_first":
        # Domain stays as classified by keywords (likely codecompass/worker/
        # ai_snake). Lock the intent to code_explanation because repo_first
        # is a code-centric weight profile.
        if intent == INTENT_GENERIC_CHAT:
            intent = INTENT_CODE_EXPLANATION

    # UI config signals: codecompass active + no domain hit → lean codecompass
    if bool(cfg.get("chat_use_codecompass")) and domain == DOMAIN_GENERIC and best_domain_hits == 0 and _trigger_mode == "auto":
        domain = DOMAIN_CODECOMPASS

    # tutorial_mode active + generic intent → tutorial help
    if bool(cfg.get("tutorial_mode")) and intent == INTENT_GENERIC_CHAT:
        intent = INTENT_TUTORIAL

    if domain != DOMAIN_GENERIC and intent == INTENT_GENERIC_CHAT and any(marker in q for marker in ("was ist", "what is")):
        intent = INTENT_CODE_EXPLANATION

    return domain, intent


# ──────────────────────────────────────────
# CRPS-004: profile lookup tables
# ──────────────────────────────────────────

_PROFILE_TABLE: dict[tuple[str, str], dict[str, Any]] = {
    (DOMAIN_CODECOMPASS, INTENT_CODE_EXPLANATION): {
        "source_types": ["repo", "artifact"],
        "source_type_weights": {"repo": 1.45, "artifact": 1.05, "wiki": 0.3, "task_memory": 0.95},
        "retrieval_intent": "code_explanation_with_codecompass",
        "negative_source_patterns": ["book-of-ananta", "book_of_ananta", "snake_tutor", "terminal-header-logo-renderer", "markdown_mermaid"],
    },
    (DOMAIN_CODECOMPASS, INTENT_ARCHITECTURE): {
        "source_types": ["repo", "artifact", "wiki"],
        "source_type_weights": {"repo": 1.1, "artifact": 1.25, "wiki": 1.15, "task_memory": 0.9},
        "retrieval_intent": "architecture_codecompass_overview",
        "negative_source_patterns": [],
    },
    (DOMAIN_CODECOMPASS, INTENT_MERMAID): {
        "source_types": ["artifact", "repo"],
        "source_type_weights": {"repo": 1.0, "artifact": 1.2, "wiki": 0.8, "task_memory": 0.85},
        "retrieval_intent": "mermaid_diagram_request",
        "negative_source_patterns": [],
    },
    (DOMAIN_CODECOMPASS, INTENT_ARCHITECTURE_FULL_SCAN): {
        "source_types": ["repo", "artifact"],
        "source_type_weights": {"repo": 1.35, "artifact": 1.1, "wiki": 0.55, "task_memory": 0.95},
        "retrieval_intent": "architecture_full_scan_codecompass",
        "negative_source_patterns": ["book-of-ananta", "book_of_ananta", "snake_tutor", "terminal-header-logo-renderer"],
    },
    (DOMAIN_WORKER, INTENT_CODE_EXPLANATION): {
        "source_types": ["repo", "artifact"],
        "source_type_weights": {"repo": 1.35, "artifact": 1.15, "wiki": 0.5, "task_memory": 1.1},
        "retrieval_intent": "worker_code_explanation",
        "negative_source_patterns": ["book-of-ananta", "book_of_ananta", "wiki_de"],
    },
    (DOMAIN_WORKER, INTENT_ARCHITECTURE): {
        "source_types": ["repo", "artifact"],
        "source_type_weights": {"repo": 1.2, "artifact": 1.2, "wiki": 0.6, "task_memory": 1.0},
        "retrieval_intent": "worker_architecture_overview",
        "negative_source_patterns": [],
    },
    (DOMAIN_WORKER, INTENT_ARCHITECTURE_FULL_SCAN): {
        "source_types": ["repo", "artifact"],
        "source_type_weights": {"repo": 1.35, "artifact": 1.05, "wiki": 0.55, "task_memory": 1.0},
        "retrieval_intent": "worker_architecture_full_scan",
        "negative_source_patterns": ["book-of-ananta", "book_of_ananta", "snake_tutor"],
    },
    (DOMAIN_AI_SNAKE, INTENT_CODE_EXPLANATION): {
        "source_types": ["repo", "artifact"],
        "source_type_weights": {"repo": 1.3, "artifact": 1.2, "wiki": 0.5, "task_memory": 0.9},
        "retrieval_intent": "snake_code_explanation",
        "negative_source_patterns": ["book-of-ananta", "book_of_ananta"],
    },
    (DOMAIN_AI_SNAKE, INTENT_GENERIC_CHAT): {
        "source_types": ["artifact", "repo"],
        "source_type_weights": {"repo": 1.0, "artifact": 1.1, "wiki": 0.9, "task_memory": 0.95},
        "retrieval_intent": "chat_codecompass_overview",
        "negative_source_patterns": [],
    },
    (DOMAIN_ANANTA_GAME, INTENT_TUTORIAL): {
        "source_types": ["artifact", "wiki"],
        "source_type_weights": {"repo": 0.7, "artifact": 1.3, "wiki": 1.2, "task_memory": 0.8},
        "retrieval_intent": "game_tutorial_docs",
        "negative_source_patterns": [],
    },
    (DOMAIN_ANANTA_GAME, INTENT_GAME_DESIGN): {
        "source_types": ["artifact", "wiki"],
        "source_type_weights": {"repo": 0.8, "artifact": 1.2, "wiki": 1.1, "task_memory": 0.9},
        "retrieval_intent": "game_design_docs",
        "negative_source_patterns": [],
    },
    (DOMAIN_ANANTA_GAME, INTENT_GENERIC_CHAT): {
        "source_types": ["artifact", "wiki"],
        "source_type_weights": {"repo": 0.75, "artifact": 1.25, "wiki": 1.15, "task_memory": 0.85},
        "retrieval_intent": "game_generic",
        "negative_source_patterns": [],
    },
    (DOMAIN_OPS, INTENT_OPS_RUNBOOK): {
        "source_types": ["artifact", "repo"],
        "source_type_weights": {"repo": 1.1, "artifact": 1.2, "wiki": 0.8, "task_memory": 1.0},
        "retrieval_intent": "ops_runbook",
        "negative_source_patterns": [],
    },
    (DOMAIN_OPERATOR_TUI, INTENT_CODE_EXPLANATION): {
        "source_types": ["repo", "artifact"],
        "source_type_weights": {"repo": 1.35, "artifact": 1.1, "wiki": 0.5, "task_memory": 0.9},
        "retrieval_intent": "tui_code_explanation",
        "negative_source_patterns": ["book-of-ananta", "book_of_ananta", "wiki_de"],
    },
}

_DOMAIN_FALLBACK: dict[str, dict[str, Any]] = {
    DOMAIN_CODECOMPASS: {
        "source_types": ["repo", "artifact"],
        "source_type_weights": {"repo": 1.2, "artifact": 1.15, "wiki": 0.7, "task_memory": 0.9},
        "retrieval_intent": "chat_codecompass_overview",
        "negative_source_patterns": [],
    },
    DOMAIN_WORKER: {
        "source_types": ["repo", "artifact"],
        "source_type_weights": {"repo": 1.25, "artifact": 1.1, "wiki": 0.6, "task_memory": 1.05},
        "retrieval_intent": "worker_generic",
        "negative_source_patterns": [],
    },
    DOMAIN_AI_SNAKE: {
        "source_types": ["artifact", "repo"],
        "source_type_weights": {"repo": 1.0, "artifact": 1.1, "wiki": 0.9, "task_memory": 0.95},
        "retrieval_intent": "chat_codecompass_overview",
        "negative_source_patterns": [],
    },
    DOMAIN_ANANTA_GAME: {
        "source_types": ["artifact", "wiki"],
        "source_type_weights": {"repo": 0.8, "artifact": 1.2, "wiki": 1.1, "task_memory": 0.9},
        "retrieval_intent": "game_generic",
        "negative_source_patterns": [],
    },
    DOMAIN_OPERATOR_TUI: {
        "source_types": ["repo", "artifact"],
        "source_type_weights": {"repo": 1.2, "artifact": 1.0, "wiki": 0.6, "task_memory": 0.9},
        "retrieval_intent": "tui_generic",
        "negative_source_patterns": [],
    },
    DOMAIN_OPS: {
        "source_types": ["artifact", "repo"],
        "source_type_weights": {"repo": 1.0, "artifact": 1.2, "wiki": 0.8, "task_memory": 1.0},
        "retrieval_intent": "ops_generic",
        "negative_source_patterns": [],
    },
}

_GENERIC_FALLBACK: dict[str, Any] = {
    "source_types": ["artifact", "repo"],
    "source_type_weights": {"repo": 1.0, "artifact": 1.08, "wiki": 1.0, "task_memory": 1.0},
    "retrieval_intent": "chat_generic",
    "negative_source_patterns": [],
}

# profile_mode overrides from chat_retrieval_profile config key
_PROFILE_MODE_OVERRIDES: dict[str, dict[str, Any]] = {
    "repo_first": {
        "source_type_weights_patch": {"repo": 1.4, "artifact": 1.0, "wiki": 0.6, "task_memory": 0.9},
        "ensure_source_type": "repo",
    },
    "docs_first": {
        "source_type_weights_patch": {"repo": 0.8, "artifact": 1.3, "wiki": 1.2, "task_memory": 0.9},
        "ensure_source_type": "artifact",
    },
    "auto": {},
    "legacy": {},
    "disabled": {},
}


def resolve_profile(
    query: str,
    ui_config: dict[str, Any] | None = None,
    *,
    domain_hint: str | None = None,
    intent_override: str | None = None,
    feature_flag: str | None = None,
) -> RetrievalProfile:
    """
    Resolve a RetrievalProfile for the given query and ui_config.

    feature_flag / chat_retrieval_profile values:
      "auto"     — full profile system (default)
      "legacy"   — generic fallback, preserves old ui-flag behaviour
      "disabled" — generic fallback, no domain logic
      "repo_first" / "docs_first" — override weights
    """
    cfg = dict(ui_config or {})
    effective_flag = str(feature_flag or cfg.get("chat_retrieval_profile") or "auto").strip().lower()

    # chat_code_questions_repo_first shortcut upgrades to repo_first mode
    if bool(cfg.get("chat_code_questions_repo_first")) and effective_flag == "auto":
        effective_flag = "repo_first"

    if effective_flag in {"legacy", "disabled"}:
        spec = dict(_GENERIC_FALLBACK)
        source_types = _apply_ui_source_constraints(list(spec["source_types"]), cfg)
        source_type_weights = dict(spec["source_type_weights"])
        negative_source_patterns: list[str] = []
        return RetrievalProfile(
            profile_id="generic_legacy",
            domain=DOMAIN_GENERIC,
            intent=INTENT_GENERIC_CHAT,
            source_types=source_types,
            source_type_weights=source_type_weights,
            retrieval_intent=str(spec["retrieval_intent"]),
            negative_source_patterns=negative_source_patterns,
            feature_flag=effective_flag,
            selected_by="retrieval_profile_resolver.v1",
            reasons=[f"feature_flag:{effective_flag}", "legacy_generic_fallback"],
            source_policy=_build_source_policy(source_types, source_type_weights, negative_source_patterns),
            chunk_policy=_default_chunk_policy(),
            expansion_policy=_default_expansion_policy(),
            explainability=_default_explainability(),
        )

    domain, intent = classify_retrieval_intent(query, cfg)
    reasons = [f"classified_domain:{domain}", f"classified_intent:{intent}"]
    # Surface the trigger_mode in reasons so the TUI Profile Inspector can
    # show WHY a particular domain/intent was chosen.
    _trigger_mode_resolved = str(cfg.get("chat_codecompass_trigger_mode") or "auto").strip().lower()
    if _trigger_mode_resolved != "auto":
        reasons.append(f"trigger_mode:{_trigger_mode_resolved}")
    full_scan = _is_full_scan_intent(query, intent, cfg)
    output_intent = _resolve_output_intent(query, intent)
    if full_scan:
        intent = INTENT_ARCHITECTURE_FULL_SCAN
        reasons.append("analysis_mode:architecture_full_scan")
        if not output_intent:
            output_intent = "architecture_overview"

    if domain_hint and str(domain_hint).strip():
        # CRPS-007: restrict domain_hint to the known DOMAIN_* constants so
        # the TUI choice values (auto/codecompass/ai_snake/worker/ananta_game/
        # operator_tui/ops/generic) map 1:1 to backend constants. Unknown
        # values fall back to the classified domain — never raise.
        _known_domains = {
            DOMAIN_CODECOMPASS, DOMAIN_AI_SNAKE, DOMAIN_WORKER,
            DOMAIN_ANANTA_GAME, DOMAIN_OPERATOR_TUI, DOMAIN_OPS, DOMAIN_GENERIC,
        }
        _hint = str(domain_hint).strip()
        if _hint in _known_domains:
            domain = _hint
            reasons.append(f"domain_hint:{domain}")
        elif _hint.lower().startswith("domain:"):
            # CCRDS-006: `domain:<id>` is a runtime-domain-scope selection,
            # not a profile domain. The hard scope is resolved separately by
            # agent.codecompass.domain_scope_resolver; the profile keeps the
            # classified domain.
            reasons.append(f"domain_hint_runtime_scope:{_hint[len('domain:'):].strip().lower()}")
        else:
            reasons.append(f"domain_hint_unknown:{_hint}:ignored")
    if intent_override and str(intent_override).strip():
        intent = str(intent_override).strip()
        reasons.append(f"intent_override:{intent}")

    spec = dict(
        _PROFILE_TABLE.get((domain, intent))
        or _DOMAIN_FALLBACK.get(domain)
        or _GENERIC_FALLBACK
    )

    # Apply profile_mode weight patches
    mode_override = _PROFILE_MODE_OVERRIDES.get(effective_flag, {})
    if mode_override:
        weights = dict(spec.get("source_type_weights") or {})
        weights.update(mode_override.get("source_type_weights_patch") or {})
        spec["source_type_weights"] = weights
        reasons.append(f"profile_mode_override:{effective_flag}")
        ensure_st = mode_override.get("ensure_source_type")
        if ensure_st and ensure_st not in list(spec.get("source_types") or []):
            spec["source_types"] = [ensure_st] + list(spec.get("source_types") or [])
            reasons.append(f"ensured_source_type:{ensure_st}")

    # Apply explicit chat_codecompass_trigger_mode patches AFTER the
    # profile_mode override so the user choice wins over chat_retrieval_profile.
    # force_repo_first: drop all source types except repo + task_memory.
    if _trigger_mode_resolved == "force_repo_first":
        spec["source_types"] = [st for st in list(spec.get("source_types") or [])
                                if st in {"repo", "task_memory"}]
        # Boost repo weight so it dominates the fusion ranking.
        weights = dict(spec.get("source_type_weights") or {})
        weights["repo"] = max(float(weights.get("repo", 1.0)), 1.5)
        spec["source_type_weights"] = weights
        reasons.append("trigger_mode_force_repo_first:source_types=repo,task_memory")
    # force_codecompass: ensure artifact (CodeCompass output) is in source_types
    # and heavily weighted, regardless of which domain/intent was classified.
    elif _trigger_mode_resolved == "force_codecompass":
        if "artifact" not in list(spec.get("source_types") or []):
            spec["source_types"] = ["artifact"] + list(spec.get("source_types") or [])
        weights = dict(spec.get("source_type_weights") or {})
        weights["artifact"] = max(float(weights.get("artifact", 1.0)), 1.4)
        spec["source_type_weights"] = weights
        reasons.append("trigger_mode_force_codecompass:ensured_artifact")

    # Apply ui_config source constraints (hard boundary)
    source_types = _apply_ui_source_constraints(list(spec.get("source_types") or []), cfg)
    source_type_weights = dict(spec.get("source_type_weights") or {})
    negative_source_patterns = list(spec.get("negative_source_patterns") or [])

    # Build warnings for sources requested but globally disabled
    warnings: list[str] = []
    requested = list(spec.get("source_types") or [])
    for st in requested:
        if st not in source_types:
            warnings.append(f"source_type_disabled_by_ui_config:{st}")
            reasons.append(f"ui_disabled_source_type:{st}")

    profile_id = f"{domain}/{intent}"
    analysis_mode = "architecture_full_scan" if full_scan else ""
    coverage_policy = "relation_expanded" if full_scan else ""
    summary_policy = "rolling_structured" if full_scan else ""
    budgets = _default_full_scan_budgets() if full_scan else {}
    return RetrievalProfile(
        profile_id=profile_id,
        domain=domain,
        intent=intent,
        source_types=source_types,
        source_type_weights=source_type_weights,
        retrieval_intent=str(spec.get("retrieval_intent") or ""),
        negative_source_patterns=negative_source_patterns,
        feature_flag=effective_flag,
        warnings=warnings,
        selected_by="retrieval_profile_resolver.v1",
        reasons=reasons,
        source_policy=_build_source_policy(source_types, source_type_weights, negative_source_patterns),
        chunk_policy=_default_chunk_policy(),
        expansion_policy=_default_expansion_policy(),
        explainability=_default_explainability(),
        analysis_mode=analysis_mode,
        output_intent=output_intent,
        coverage_policy=coverage_policy,
        summary_policy=summary_policy,
        budgets=budgets,
    )


def _apply_ui_source_constraints(source_types: list[str], cfg: dict[str, Any]) -> list[str]:
    """Remove source types disabled by ui_config flags.

    CRPS-007: `chat_include_task_memory` is the user-facing toggle for the
    `task_memory` source type. There is no legacy equivalent — the
    `task_memory` source was previously always-on. Default: True (preserve
    old behaviour for users upgrading).
    """
    result = list(source_types)
    if not bool(cfg.get("chat_use_codecompass", True)):
        result = [st for st in result if st != "artifact"]
    if not bool(cfg.get("chat_include_local_project", True)):
        result = [st for st in result if st != "repo"]
    if not bool(cfg.get("chat_include_wikipedia", False)):
        result = [st for st in result if st != "wiki"]
    # Default for the new key: True (opt-out rather than opt-in) so existing
    # users see no behaviour change.
    if not bool(cfg.get("chat_include_task_memory", True)):
        result = [st for st in result if st != "task_memory"]
    return result


# ──────────────────────────────────────────
# CRPS-009: negative source pattern filter
# ──────────────────────────────────────────

def apply_profile_source_constraints(
    chunks: list[dict[str, Any]],
    profile: RetrievalProfile,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Filter/penalize chunks whose metadata matches profile.negative_source_patterns.
    Returns (filtered_chunks, filter_meta).
    """
    if not profile.negative_source_patterns:
        return list(chunks), {"removed": 0, "patterns": [], "insufficient_positive_sources": False}

    patterns = [str(p).lower() for p in profile.negative_source_patterns if str(p).strip()]
    removed = 0
    filtered: list[dict[str, Any]] = []
    for chunk in chunks:
        metadata = dict((chunk or {}).get("metadata") or {})
        source_id = str(metadata.get("source_id") or "").lower()
        record_kind = str(metadata.get("record_kind") or "").lower()
        collection = str(metadata.get("collection_name") or "").lower()
        source_path = str((chunk or {}).get("source") or "").lower()
        haystack = f"{source_id} {record_kind} {collection} {source_path}"
        if any(pat in haystack for pat in patterns):
            removed += 1
        else:
            filtered.append(chunk)

    insufficient = len(filtered) == 0 and removed > 0
    return filtered, {
        "removed": removed,
        "patterns": patterns,
        "insufficient_positive_sources": insufficient,
    }
