"""APMCO-002: Configuration model for PreModelContextOrchestrator.

Config key: ``pre_model_context``
Default: disabled / fully backward-compatible. No existing flow is touched
unless a surface or the top-level key explicitly enables the orchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Mode constants ────────────────────────────────────────────────────────────
MODE_DISABLED = "disabled"
MODE_OBSERVE_ONLY = "observe_only"
MODE_WORKER_DECIDES = "worker_decides"
MODE_PREFER_CONTEXT = "prefer_context"
MODE_CONTEXT_FIRST = "context_first"
MODE_PREFER_DETERMINISTIC = "prefer_deterministic"
MODE_DETERMINISTIC_ONLY = "deterministic_only"

VALID_MODES = frozenset({
    MODE_DISABLED,
    MODE_OBSERVE_ONLY,
    MODE_WORKER_DECIDES,
    MODE_PREFER_CONTEXT,
    MODE_CONTEXT_FIRST,
    MODE_PREFER_DETERMINISTIC,
    MODE_DETERMINISTIC_ONLY,
})

# ── Task kind constants ───────────────────────────────────────────────────────
TASK_NAVIGATION = "navigation"
TASK_EXPLANATION = "explanation"
TASK_BUGFIX = "bugfix"
TASK_IMPLEMENTATION = "implementation"
TASK_TEST = "test"
TASK_ARCHITECTURE = "architecture"
TASK_SECURITY = "security"
TASK_CONFIG = "config"
TASK_GENERIC_CHAT = "generic_chat"

ALL_TASK_KINDS = frozenset({
    TASK_NAVIGATION, TASK_EXPLANATION, TASK_BUGFIX, TASK_IMPLEMENTATION,
    TASK_TEST, TASK_ARCHITECTURE, TASK_SECURITY, TASK_CONFIG, TASK_GENERIC_CHAT,
})

# Task kinds that can be answered deterministically (no LLM needed)
DETERMINISTIC_TASK_KINDS = frozenset({TASK_NAVIGATION})

# Task kinds that must have evidence before answering (no hallucination)
HIGH_EVIDENCE_TASK_KINDS = frozenset({TASK_SECURITY, TASK_BUGFIX})

# Keywords used by the heuristic task classifier
_NAV_KW = frozenset({
    "wo ", "where ", "welche datei", "which file", "pfad", "path",
    "navigate", "navigiere", "finde die datei", "find the file",
    "zeig mir die datei", "show me the file",
})
_BUGFIX_KW = frozenset({
    "bug", "fehler", "error", "fix", "repariere", "broken", "kaputt",
    "traceback", "exception", "crash", "absturz",
})
_SECURITY_KW = frozenset({
    "security", "sicherheit", "authentication", "password", "passwort",
    "secret", "geheimnis", "token", "vulnerability", "verwundbar", "exploit",
    "injection", "xss", "csrf", "sql injection", "sicherheitslücke",
    "sicherheitsproblem", "angriff", "attack",
})
_IMPL_KW = frozenset({
    "implement", "implementiere", "schreibe", "write", "create", "erstelle",
    "add function", "füge funktion", "neue funktion", "new function",
    "build", "baue",
})
_TEST_KW = frozenset({
    "test", "unittest", "pytest", "spec", "fixture", "mock",
    "coverage", "abdeckung",
})
_ARCH_KW = frozenset({
    "architektur", "architecture", "design", "struktur", "structure",
    "overview", "überblick", "system", "komponenten", "components",
    "how does", "wie funktioniert",
})
_CONFIG_KW = frozenset({
    "config", "konfiguration", "setting", "einstellung", "yaml", "toml",
    "env", "environment", "variable",
})


def classify_task(task_text: str, explicit_task_kind: str | None = None) -> str:
    """Heuristic task classifier — no LLM required.

    Returns one of the ``TASK_*`` constants. ``explicit_task_kind`` overrides
    everything if it is a recognised value.
    """
    if explicit_task_kind and explicit_task_kind in ALL_TASK_KINDS:
        return explicit_task_kind
    low = task_text.lower()
    if any(kw in low for kw in _NAV_KW):
        return TASK_NAVIGATION
    if any(kw in low for kw in _SECURITY_KW):
        return TASK_SECURITY
    if any(kw in low for kw in _BUGFIX_KW):
        return TASK_BUGFIX
    if any(kw in low for kw in _TEST_KW):
        return TASK_TEST
    if any(kw in low for kw in _IMPL_KW):
        return TASK_IMPLEMENTATION
    if any(kw in low for kw in _ARCH_KW):
        return TASK_ARCHITECTURE
    if any(kw in low for kw in _CONFIG_KW):
        return TASK_CONFIG
    if "?" not in task_text and len(task_text.split()) < 8:
        return TASK_NAVIGATION
    if any(kw in low for kw in _ARCH_KW):
        return TASK_EXPLANATION
    return TASK_GENERIC_CHAT


# ── Surface config ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SurfaceConfig:
    enabled: bool = False
    mode: str = MODE_DISABLED
    reuse_existing_chat_context_flow: bool = True
    migrate_to_shared_orchestrator: bool = False

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "SurfaceConfig":
        d = dict(raw or {})
        mode = str(d.get("mode") or MODE_DISABLED).strip().lower()
        if mode not in VALID_MODES:
            mode = MODE_DISABLED
        return cls(
            enabled=bool(d.get("enabled", False)),
            mode=mode,
            reuse_existing_chat_context_flow=bool(d.get("reuse_existing_chat_context_flow", True)),
            migrate_to_shared_orchestrator=bool(d.get("migrate_to_shared_orchestrator", False)),
        )


# ── Ranking weights ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RankingWeights:
    embedding_score: float = 0.28
    symbol_match_score: float = 0.15
    graph_distance_score: float = 0.10
    working_file_bonus: float = 0.30
    domain_scope_bonus: float = 0.08
    test_relation_bonus: float = 0.05
    recency_bonus: float = 0.05
    policy_penalty: float = -0.20
    sensitivity_penalty: float = -0.15

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "RankingWeights":
        d = dict(raw or {})

        def _f(k: str, default: float) -> float:
            try:
                return float(d.get(k, default))
            except (TypeError, ValueError):
                return default

        return cls(
            embedding_score=_f("embedding_score", cls.embedding_score),
            symbol_match_score=_f("symbol_match_score", cls.symbol_match_score),
            graph_distance_score=_f("graph_distance_score", cls.graph_distance_score),
            working_file_bonus=_f("working_file_bonus", cls.working_file_bonus),
            domain_scope_bonus=_f("domain_scope_bonus", cls.domain_scope_bonus),
            test_relation_bonus=_f("test_relation_bonus", cls.test_relation_bonus),
            recency_bonus=_f("recency_bonus", cls.recency_bonus),
            policy_penalty=_f("policy_penalty", cls.policy_penalty),
            sensitivity_penalty=_f("sensitivity_penalty", cls.sensitivity_penalty),
        )


# ── Top-level config ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PreModelContextConfig:
    enabled: bool = False
    mode: str = MODE_DISABLED
    surfaces: dict[str, SurfaceConfig] = field(default_factory=dict)
    allow_no_llm_answers: bool = True
    allow_direct_llm_without_context: bool = True
    cache_enabled: bool = True
    trace_enabled: bool = True
    ranking_weights: RankingWeights = field(default_factory=RankingWeights)
    context_budget_chars: int = 12_000

    @classmethod
    def from_raw(cls, config: dict[str, Any] | None) -> "PreModelContextConfig":
        """Parse from the top-level Ananta config dict."""
        raw = dict((config or {}).get("pre_model_context") or {})
        mode = str(raw.get("mode") or MODE_DISABLED).strip().lower()
        if mode not in VALID_MODES:
            mode = MODE_DISABLED

        surfaces: dict[str, SurfaceConfig] = {}
        for name, surf_raw in (raw.get("surfaces") or {}).items():
            surfaces[str(name)] = SurfaceConfig.from_raw(surf_raw)

        try:
            budget = max(1_000, int(raw.get("context_budget_chars") or 12_000))
        except (TypeError, ValueError):
            budget = 12_000

        return cls(
            enabled=bool(raw.get("enabled", False)),
            mode=mode,
            surfaces=surfaces,
            allow_no_llm_answers=bool(raw.get("allow_no_llm_answers", True)),
            allow_direct_llm_without_context=bool(raw.get("allow_direct_llm_without_context", True)),
            cache_enabled=bool(raw.get("cache_enabled", True)),
            trace_enabled=bool(raw.get("trace_enabled", True)),
            ranking_weights=RankingWeights.from_raw(raw.get("ranking_weights")),
            context_budget_chars=budget,
        )

    def resolve_surface_mode(self, surface: str) -> str:
        """Return the effective mode for a named surface.

        Priority: surface-specific config (if enabled) → top-level mode.
        If the orchestrator is globally disabled (``enabled=False``) and no
        surface overrides it, ``MODE_DISABLED`` is returned.
        """
        surf = self.surfaces.get(surface)
        if surf and surf.enabled and surf.mode in VALID_MODES:
            return surf.mode
        if self.enabled and self.mode in VALID_MODES:
            return self.mode
        return MODE_DISABLED
