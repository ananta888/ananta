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
    )


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

    # UI config signals: codecompass active + no domain hit → lean codecompass
    if bool(cfg.get("chat_use_codecompass")) and domain == DOMAIN_GENERIC and best_domain_hits == 0:
        domain = DOMAIN_CODECOMPASS

    # tutorial_mode active + generic intent → tutorial help
    if bool(cfg.get("tutorial_mode")) and intent == INTENT_GENERIC_CHAT:
        intent = INTENT_TUTORIAL

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
        return RetrievalProfile(
            profile_id="generic_legacy",
            domain=DOMAIN_GENERIC,
            intent=INTENT_GENERIC_CHAT,
            source_types=source_types,
            source_type_weights=dict(spec["source_type_weights"]),
            retrieval_intent=str(spec["retrieval_intent"]),
            negative_source_patterns=[],
            feature_flag=effective_flag,
        )

    domain, intent = classify_retrieval_intent(query, cfg)

    if domain_hint and str(domain_hint).strip():
        domain = str(domain_hint).strip()
    if intent_override and str(intent_override).strip():
        intent = str(intent_override).strip()

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
        ensure_st = mode_override.get("ensure_source_type")
        if ensure_st and ensure_st not in list(spec.get("source_types") or []):
            spec["source_types"] = [ensure_st] + list(spec.get("source_types") or [])

    # Apply ui_config source constraints (hard boundary)
    source_types = _apply_ui_source_constraints(list(spec.get("source_types") or []), cfg)

    # Build warnings for sources requested but globally disabled
    warnings: list[str] = []
    requested = list(spec.get("source_types") or [])
    for st in requested:
        if st not in source_types:
            warnings.append(f"source_type_disabled_by_ui_config:{st}")

    profile_id = f"{domain}/{intent}"
    return RetrievalProfile(
        profile_id=profile_id,
        domain=domain,
        intent=intent,
        source_types=source_types,
        source_type_weights=dict(spec.get("source_type_weights") or {}),
        retrieval_intent=str(spec.get("retrieval_intent") or ""),
        negative_source_patterns=list(spec.get("negative_source_patterns") or []),
        feature_flag=effective_flag,
        warnings=warnings,
    )


def _apply_ui_source_constraints(source_types: list[str], cfg: dict[str, Any]) -> list[str]:
    """Remove source types disabled by ui_config flags."""
    result = list(source_types)
    if not bool(cfg.get("chat_use_codecompass", True)):
        result = [st for st in result if st != "artifact"]
    if not bool(cfg.get("chat_include_local_project", True)):
        result = [st for st in result if st != "repo"]
    if not bool(cfg.get("chat_include_wikipedia", False)):
        result = [st for st in result if st != "wiki"]
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
