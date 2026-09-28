"""The context window as one central, switchable profile, and the budgets derived from it.

Configured context window
          |
Provider / model capability (llama.cpp /props, LM Studio /v1/models, Ollama /api/show, llm_config)
          |
Effective context window  = the smallest of all known limits, never more than configured
          |
ContextBudgets            = every window-dependent budget as a share of the effective window

**Configured window** -- in this order: runtime config ``context_window.tokens`` (custom),
runtime config ``context_window.profile`` (settings UI), ``ANANTA_CONTEXT_TOKENS`` when set
explicitly, ``ANANTA_CONTEXT_PROFILE`` (default ``standard_32k``).

**Budgets** are shares of the window calibrated at 32k: every former fixed value that really
depended on the window (bundle 4096/12288/16384, tool results, planning segments, recovery,
RAG context, compactor output, ...) is its 32k value divided by 32768, so 32k behaves exactly as
before and 64k/128k scale. Every budget is capped by what is left of the window after the output
reserve, a safety margin for estimation errors and the fixed request part (system prompt, tool
definitions, task), so no single consumer can take the window.

**Explicit overrides** stay possible (``budget_override``) but never exceed the derived room; a
stored historical default (e.g. ``max_tool_result_chars: 8000`` persisted from old defaults) counts
as "not set", otherwise existing installations would not grow with a larger profile.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

PROFILES: dict[str, int] = {
    "compact_12k": 12288,
    "standard_32k": 32768,
    "full_64k": 65536,
    "extended_128k": 131072,
}
CUSTOM_PROFILE = "custom"
DEFAULT_PROFILE = "standard_32k"
CALIBRATION_WINDOW = 32768  # the shares below reproduce the former fixed values at this window
MIN_WINDOW, MAX_WINDOW = 2048, 1_048_576
CHARS_PER_TOKEN = 4

# Shares of the effective window (tokens), calibrated at 32k: the comment is the former fixed value.
SHARES: dict[str, float] = {
    "bundle_compact": 4096 / CALIBRATION_WINDOW,      # context bundle, mode compact
    "bundle_standard": 12288 / CALIBRATION_WINDOW,    # context bundle, mode standard
    "bundle_full": 16384 / CALIBRATION_WINDOW,        # context bundle, mode full
    "evidence": 0.45,                                 # CodeCompass planner / agentic retrieval / RLM synthesis
    "rag_context": 3000 / CALIBRATION_WINDOW,         # hybrid RAG context (RAG_MAX_CONTEXT_TOKENS)
    "tool_result": 2000 / CALIBRATION_WINDOW,         # one tool result (8000 chars)
    "tool_results_total": 0.5,                        # worker tool-loop results (half the window)
    "diff": 3000 / CALIBRATION_WINDOW,                # workspace-mutation diff (12000 chars)
    "planning_context": 6000 / CALIBRATION_WINDOW,    # planning segments: 3 x 8000 chars
    "planning_segment": 2000 / CALIBRATION_WINDOW,    # one planning segment (8000 chars)
    "compactor_output": 3000 / CALIBRATION_WINDOW,    # propose/recovery compactor output (12000 chars)
    "recovery_context": 0.25,                         # recovery context: a quarter of the window
    "editor_conversation": 12000 / CALIBRATION_WINDOW,  # visual-process editor, conversation prompt (12000)
    "curation": 10000 / CALIBRATION_WINDOW,           # context curation pipeline (40000 chars)
    "worker_file": 1000 / CALIBRATION_WINDOW,         # worker batch loop: one file excerpt (4000 chars)
    "worker_snippet": 2000 / CALIBRATION_WINDOW,      # worker batch loop: one snippet (8000 chars)
    "snake_catalog": 5000 / CALIBRATION_WINDOW,       # snake RAG: component catalog (20000 chars)
    "snake_tool_file": 5000 / CALIBRATION_WINDOW,     # snake RAG: one tool file read (20000 chars)
}
# The same values as they were hard-coded or persisted before (unit as stored): treated as "not set".
LEGACY_DEFAULTS: dict[str, int] = {
    "tool_result_chars": 8000,
    "diff_chars": 12000,
    "planning_segment_chars": 8000,
    "compactor_output_chars": 12000,
    "rag_context_tokens": 3000,
    "rag_context_chars": 12000,
    "evidence_tokens": 12000,
    "llm_context_limit": 32768,
    "chat_context_chars": 12000,
    "pre_model_context_chars": 12000,
    "curation_chars": 40000,
    "worker_file_chars": 4000,
    "worker_snippet_chars": 8000,
    "snake_catalog_chars": 20000,
    "snake_tool_file_chars": 20000,
}
DEFAULT_REQUEST_OVERHEAD_TOKENS = 12000  # measured live: tool definitions + AGENTS.md + system prompt
_log = logging.getLogger("ananta.context_profile")


# --- configured window ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfiguredWindow:
    tokens: int
    profile: str  # a PROFILES key or "custom"
    source: str  # runtime_tokens | runtime_profile | env_tokens | env_profile | default


def profile_for_tokens(tokens: int) -> str:
    for name, value in PROFILES.items():
        if value == int(tokens):
            return name
    return CUSTOM_PROFILE


def nearest_profile(tokens: int) -> str:
    """The largest profile not above ``tokens`` (``compact_12k`` below that) -- for describing a window size."""
    fitting = [name for name, value in sorted(PROFILES.items(), key=lambda item: item[1]) if value <= int(tokens)]
    return fitting[-1] if fitting else "compact_12k"


def normalize_profile(value: Any) -> str | None:
    """A known profile name (also ``32k``/``64k``/``128k``/``custom``), else ``None``."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    aliases = {"12k": "compact_12k", "32k": "standard_32k", "64k": "full_64k", "128k": "extended_128k"}
    text = aliases.get(text, text)
    return text if text in PROFILES or text == CUSTOM_PROFILE else None


def _valid_tokens(value: Any) -> int | None:
    try:
        tokens = int(value)
    except (TypeError, ValueError):
        return None
    return tokens if MIN_WINDOW <= tokens <= MAX_WINDOW else None


def _agent_config(agent_cfg: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if agent_cfg is not None:
        return agent_cfg
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            return current_app.config.get("AGENT_CONFIG", {}) or {}
    except Exception:  # noqa: BLE001 -- outside Flask: environment only
        pass
    return {}


# The window the Hub assigned to the task a worker is executing (hub -> worker, per request). The Hub owns the
# window decision: it sized the task for it, so the worker's budgets and guardrails must use the same window.
_ASSIGNED_WINDOW: ContextVar[dict[str, Any] | None] = ContextVar("assigned_context_window", default=None)


def hub_assignment(agent_cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The Hub's configured window as it is handed to a worker with a task (``context_window`` field)."""
    configured = configured_window(agent_cfg)
    if configured.profile in PROFILES:
        return {"profile": configured.profile, "tokens": None}
    return {"profile": CUSTOM_PROFILE, "tokens": configured.tokens}


@contextmanager
def assigned_window_scope(raw: Any) -> Iterator[dict[str, Any] | None]:
    """Within this scope the Hub-assigned window is the configured window (invalid or empty: ignored)."""
    try:
        assigned = normalize_context_window_config(raw) or None
    except ValueError:
        _log.warning("ignoring invalid assigned context window: %r", raw)
        assigned = None
    token = _ASSIGNED_WINDOW.set(assigned)
    try:
        yield assigned
    finally:
        _ASSIGNED_WINDOW.reset(token)


def configured_window(agent_cfg: Mapping[str, Any] | None = None, *, settings: Any = None) -> ConfiguredWindow:
    """The window Ananta is configured for (before provider limits); on a worker executing a task the window
    the Hub assigned to it."""
    if settings is None:
        from agent.config import settings as _settings

        settings = _settings
    assigned = _ASSIGNED_WINDOW.get()
    if assigned:
        if assigned.get("profile") in PROFILES:
            return ConfiguredWindow(PROFILES[assigned["profile"]], assigned["profile"], "hub_assignment")
        if assigned.get("tokens"):
            tokens = int(assigned["tokens"])
            return ConfiguredWindow(tokens, profile_for_tokens(tokens), "hub_assignment")
    runtime = _agent_config(agent_cfg).get("context_window")
    if isinstance(runtime, Mapping):
        tokens = _valid_tokens(runtime.get("tokens"))
        profile = normalize_profile(runtime.get("profile"))
        if tokens is not None and profile in (None, CUSTOM_PROFILE):
            return ConfiguredWindow(tokens, profile_for_tokens(tokens), "runtime_tokens")
        if profile in PROFILES:
            return ConfiguredWindow(PROFILES[profile], profile, "runtime_profile")
    explicit_tokens = "default_context_tokens" in getattr(settings, "model_fields_set", set())
    env_tokens = _valid_tokens(getattr(settings, "default_context_tokens", None))
    if explicit_tokens and env_tokens is not None:
        return ConfiguredWindow(env_tokens, profile_for_tokens(env_tokens), "env_tokens")
    env_profile = normalize_profile(getattr(settings, "context_profile", None))
    if env_profile in PROFILES:
        return ConfiguredWindow(PROFILES[env_profile], env_profile, "env_profile")
    if env_tokens is not None:  # custom profile without ANANTA_CONTEXT_TOKENS, or an old default
        return ConfiguredWindow(env_tokens, profile_for_tokens(env_tokens), "default")
    return ConfiguredWindow(PROFILES[DEFAULT_PROFILE], DEFAULT_PROFILE, "default")


# --- provider / model limits ------------------------------------------------------------------------------


class ProviderLimitProbe:
    """The context limit a provider actually serves, asked once and cached (best effort, never raises).

    llama.cpp: ``/props`` ``default_generation_settings.n_ctx`` (what one request may use);
    LM Studio: ``context_length`` of the model in ``/v1/models``; Ollama: ``/api/show`` model_info
    ``*.context_length``. Others (OpenAI-compatible cloud, CLI backends) report nothing: the
    configured window applies.
    """

    TTL_SECONDS = 300.0

    def __init__(self, *, http_json: Callable[..., Any] | None = None, clock: Callable[[], float] = time.monotonic,
                 timeout: float = 2.0) -> None:
        self._http_json = http_json or _http_json
        self._clock = clock
        self._timeout = timeout
        self._cache: dict[tuple[str, str, str], tuple[float, int | None]] = {}
        self._lock = threading.Lock()

    def limit(self, provider: str | None, base_url: str | None, model: str | None = None) -> int | None:
        provider = str(provider or "").strip().lower()
        base = str(base_url or "").strip().rstrip("/")
        if provider not in {"llamacpp", "lmstudio", "lm_studio", "ollama"} or not base:
            return None
        key = (provider, base, str(model or ""))
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and now - cached[0] < self.TTL_SECONDS:
                return cached[1]
        try:
            value = self._ask(provider, base, str(model or ""))
        except Exception as exc:  # noqa: BLE001 -- no limit known: the configured window applies
            _log.debug("context limit probe failed for %s: %s", provider, exc)
            value = None
        value = value if type(value) is int and value > 0 else None
        with self._lock:
            self._cache[key] = (now, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _ask(self, provider: str, base: str, model: str) -> int | None:
        root = base[:-3] if base.endswith("/v1") else base
        if provider == "llamacpp":
            props = self._http_json("GET", root + "/props", None, self._timeout) or {}
            return _int((props.get("default_generation_settings") or {}).get("n_ctx")) or _int(props.get("n_ctx"))
        if provider in {"lmstudio", "lm_studio"}:
            payload = self._http_json("GET", root + "/v1/models", None, self._timeout) or {}
            rows = [row for row in payload.get("data") or [] if isinstance(row, Mapping)]
            chosen = next((row for row in rows if model and row.get("id") == model), rows[0] if rows else {})
            return _int(chosen.get("loaded_context_length") or chosen.get("context_length")
                        or chosen.get("max_context_length") or chosen.get("n_ctx"))
        if provider == "ollama" and model:
            show = self._http_json("POST", root + "/api/show", {"model": model}, self._timeout) or {}
            info = show.get("model_info") or {}
            for key, value in info.items():
                if str(key).endswith(".context_length"):
                    return _int(value)
        return None


def _int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


PROBE_ENV = "ANANTA_CONTEXT_PROVIDER_PROBE"  # "0"/"off": never ask a runtime (tests, air-gapped setups)


def _http_json(method: str, url: str, payload: Any, timeout: float) -> Any:
    import os

    import requests

    if str(os.environ.get(PROBE_ENV, "1")).strip().lower() in {"0", "false", "off", "no"}:
        return None
    session = requests.Session()
    session.trust_env = False  # local runtimes only: no proxy
    response = session.request(method, url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


_PROBE = ProviderLimitProbe()


def provider_limit_probe() -> ProviderLimitProbe:
    return _PROBE


def set_provider_limit_probe(probe: ProviderLimitProbe) -> None:
    """Replace the probe (tests, alternative transports)."""
    global _PROBE
    _PROBE = probe


def _provider_base_url(provider: str, agent_cfg: Mapping[str, Any]) -> str | None:
    try:
        from agent.local_llm_backends import resolve_local_openai_backend

        entry = resolve_local_openai_backend(provider, agent_cfg=dict(agent_cfg))
        if entry and entry.get("base_url"):
            return str(entry["base_url"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            url = (current_app.config.get("PROVIDER_URLS") or {}).get(provider)
            if url:
                return str(url)
    except Exception:  # noqa: BLE001
        pass
    llm = agent_cfg.get("llm_config") or {}
    if str(llm.get("provider") or "").strip().lower() == provider and llm.get("base_url"):
        return str(llm["base_url"])
    return None


def _declared_model_limit(agent_cfg: Mapping[str, Any], provider: str | None, model: str | None) -> int | None:
    """``llm_config.context_limit`` for its provider (the seeded 32768 counts as not set) and the model map."""
    limits = []
    llm = agent_cfg.get("llm_config") or {}
    declared = _valid_tokens(llm.get("context_limit"))
    same_provider = not provider or str(llm.get("provider") or "").strip().lower() in {"", str(provider).lower()}
    if declared is not None and declared != LEGACY_DEFAULTS["llm_context_limit"] and same_provider:
        limits.append(declared)
    if model:
        try:
            from agent.config import lookup_model_context_tokens

            mapped = _valid_tokens(lookup_model_context_tokens(model))
            if mapped is not None:
                limits.append(mapped)
        except Exception:  # noqa: BLE001
            pass
    return min(limits) if limits else None


# --- effective window -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EffectiveWindow:
    tokens: int
    configured: ConfiguredWindow
    limits: dict[str, int] = field(default_factory=dict)  # source -> limit that was known
    limited_by: str = "configured"

    def to_mapping(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "limited_by": self.limited_by, "limits": dict(self.limits),
                "configured": {"tokens": self.configured.tokens, "profile": self.configured.profile,
                               "source": self.configured.source}}


def combine_limits(*limits: int | None) -> int | None:
    """The smallest known positive limit (``None`` when none is known)."""
    known = [int(value) for value in limits if type(value) is int and value > 0]
    return min(known) if known else None


def effective_window(*, provider: str | None = None, model: str | None = None,
                     limits: Mapping[str, int | None] | None = None, agent_cfg: Mapping[str, Any] | None = None,
                     probe: bool = True, settings: Any = None) -> EffectiveWindow:
    """The window a request may really use: the smallest of configured, model, provider and runtime limits."""
    cfg = _agent_config(agent_cfg)
    configured = configured_window(cfg, settings=settings)
    if provider is None:
        if settings is None:
            from agent.config import settings as _settings

            settings = _settings
        provider = str((cfg.get("llm_config") or {}).get("provider") or cfg.get("default_provider")
                       or getattr(settings, "default_provider", "") or "") or None
    known: dict[str, int] = {"configured": configured.tokens}
    declared = _declared_model_limit(cfg, provider, model)
    if declared is not None:
        known["model"] = declared
    if probe and provider:
        served = provider_limit_probe().limit(provider, _provider_base_url(str(provider).lower(), cfg), model)
        if served is not None:
            known["provider"] = served
    for source, value in (limits or {}).items():
        if type(value) is int and value > 0:
            known[str(source)] = int(value)
    limited_by = min(known, key=lambda source: (known[source], source != "configured"))
    return EffectiveWindow(known[limited_by], configured, known, limited_by)


def effective_window_tokens(**kwargs: Any) -> int:
    return effective_window(**kwargs).tokens


# --- local runtimes vs. subscription / cloud models -------------------------------------------------------

LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio", "lm_studio", "llamacpp"})
# Cloud/subscription model families as CLI agents and APIs serve them (prompt tokens). The Ananta profile
# describes the window Ananta sizes its own prompts for on local runtimes; it never limits these.
CLOUD_MODEL_LIMITS = {"claude": 200_000, "anthropic": 200_000, "codex": 272_000, "gpt-5": 272_000,
                      "openai": 128_000, "gemini": 1_000_000}
CLOUD_PROVIDER_PREFIXES = frozenset({"anthropic", "openai", "gemini", "groq", "openrouter", "bedrock", "azure",
                                     "vertexai", "copilot", "opencode", "google", "mistral", "deepseek", "xai"})


def is_local_provider(provider: str | None, agent_cfg: Mapping[str, Any] | None = None) -> bool:
    """A model served by a local runtime (Ollama, LM Studio, llama.cpp, a configured local OpenAI-compatible
    backend) -- the only case the Ananta context profile applies to."""
    name = str(provider or "").strip().lower()
    if not name:
        return False
    if name in LOCAL_PROVIDERS:
        return True
    for entry in _agent_config(agent_cfg).get("local_openai_backends") or []:
        if isinstance(entry, Mapping) and str(entry.get("id") or entry.get("provider") or "").strip().lower() == name:
            return True
    return False


def cloud_model_limit(model: str | None, default: int | None = None) -> int | None:
    """The prompt limit of a cloud/subscription model family (``anthropic/claude-...``, ``gpt-5-codex``, ...)."""
    text = str(model or "").strip().lower()
    for key, value in CLOUD_MODEL_LIMITS.items():
        if key in text:
            return value
    return default


def window_for_provider(provider: str | None, *, model: str | None = None, requested: int | None = None,
                        agent_cfg: Mapping[str, Any] | None = None) -> int | None:
    """The token window a model call may use. Local runtimes: the effective window (profile capped by what the
    runtime serves), a requested limit only narrows it. Cloud/subscription providers: not bounded by the Ananta
    profile -- only a requested limit or a limit declared for that provider/model (``None``: the provider's own)."""
    if is_local_provider(provider, agent_cfg):
        window = effective_window_tokens(provider=str(provider).lower(), model=model or None, agent_cfg=agent_cfg)
        return max(256, min(window, int(requested))) if requested else window
    if requested:
        return max(256, int(requested))
    return _declared_model_limit(_agent_config(agent_cfg), provider, None)


# --- budget policy ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextBudgets:
    """Every window-dependent budget, derived from one effective window. All values in tokens."""

    window: int
    request_overhead: int = DEFAULT_REQUEST_OVERHEAD_TOKENS

    @property
    def output_reserve(self) -> int:
        """Room for the answer: 1/16 of the window, 1024..8192 (32k: 2048)."""
        return max(1024, min(8192, self.window // 16))

    @property
    def safety_margin(self) -> int:
        """Estimation error of the ~4 chars/token estimate: 1/16 of the window, at least 512. Measured with the
        llama.cpp tokenizer (2026-09-28): Python 4.3, German docs 3.9, TypeScript 3.8, JSON 3.4 chars/token --
        the estimate is up to ~6 % low on prose/code (covered) and ~15 % low on pure JSON (not fully)."""
        return max(512, self.window // 16)

    @property
    def fixed_overhead(self) -> int:
        """System prompt, tool definitions and task text of a request; at most 40 % of the window."""
        return max(0, min(int(self.request_overhead), (self.window * 2) // 5))

    @property
    def input_budget(self) -> int:
        """Everything a prompt may contain: window minus output reserve and safety margin."""
        return max(0, self.window - self.output_reserve - self.safety_margin)

    @property
    def available(self) -> int:
        """What is left for material (retrieval, evidence, tool results, notes) next to the fixed part."""
        return max(0, self.input_budget - self.fixed_overhead)

    def tokens(self, name: str) -> int:
        """A named budget: its share of the window, never more than ``available``."""
        return max(1, min(int(self.window * SHARES[name]), self.available))

    def chars(self, name: str) -> int:
        return self.tokens(name) * CHARS_PER_TOKEN

    def bundle_tokens(self, mode: str) -> int:
        key = {"compact": "bundle_compact", "full": "bundle_full"}.get(str(mode or "").lower(), "bundle_standard")
        return self.tokens(key)

    def clamp_tokens(self, value: Any) -> int:
        """An explicit token budget, never more than what is available in this window."""
        try:
            requested = int(value)
        except (TypeError, ValueError):
            return self.available
        return max(1, min(requested, self.available))

    def validate(self) -> None:
        """Invariant: the fixed part, reserves and the largest single budget never exceed the window."""
        largest = max(self.tokens(name) for name in SHARES)
        total = self.output_reserve + self.safety_margin + self.fixed_overhead + largest
        if total > self.window:
            raise ValueError(f"context_budget_exceeds_window:{total}>{self.window}")

    def to_mapping(self) -> dict[str, Any]:
        return {"window_tokens": self.window, "output_reserve_tokens": self.output_reserve,
                "safety_margin_tokens": self.safety_margin, "fixed_overhead_tokens": self.fixed_overhead,
                "input_budget_tokens": self.input_budget, "available_tokens": self.available,
                "budgets_tokens": {name: self.tokens(name) for name in SHARES}}


def request_overhead_tokens(agent_cfg: Mapping[str, Any] | None = None) -> int:
    """``context_strategy.request_overhead_tokens`` (the measured fixed request part), default 12000."""
    raw = (_agent_config(agent_cfg).get("context_strategy") or {}).get("request_overhead_tokens")
    try:
        return max(0, min(int(raw), 1_000_000))
    except (TypeError, ValueError):
        return DEFAULT_REQUEST_OVERHEAD_TOKENS


def context_budgets(window: int | None = None, *, agent_cfg: Mapping[str, Any] | None = None,
                    **window_kwargs: Any) -> ContextBudgets:
    """Budgets for ``window`` (default: the effective window of the configured provider)."""
    if window is None:
        window = effective_window(agent_cfg=agent_cfg, **window_kwargs).tokens
    return ContextBudgets(int(window), request_overhead_tokens(agent_cfg))


def normalize_context_window_config(raw: Any) -> dict[str, Any]:
    """Validate a ``context_window`` config update: ``{"profile": <profile>}`` or ``{"profile": "custom",
    "tokens": N}`` (``{"tokens": N}`` alone means custom). ``None``/``{}`` resets to the environment."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("context_window_object_required")
    profile = normalize_profile(raw.get("profile")) if raw.get("profile") not in (None, "") else None
    if raw.get("profile") not in (None, "") and profile is None:
        raise ValueError("context_window_profile_invalid")
    tokens = raw.get("tokens")
    if profile in PROFILES:
        return {"profile": profile, "tokens": None}
    valid = _valid_tokens(tokens)
    if valid is None:
        raise ValueError("context_window_tokens_invalid")
    return {"profile": CUSTOM_PROFILE, "tokens": valid}


def describe_context_window(agent_cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Configured, detected and effective window plus the derived budgets (settings UI, read models)."""
    window = effective_window(agent_cfg=agent_cfg)
    budgets = ContextBudgets(window.tokens, request_overhead_tokens(agent_cfg))
    return {
        "schema": "ananta.context_window.v1",
        "profiles": dict(PROFILES),
        "configured": {"profile": window.configured.profile, "tokens": window.configured.tokens,
                       "source": window.configured.source},
        "detected_limits": {k: v for k, v in window.limits.items() if k != "configured"},
        "effective": {"tokens": window.tokens, "profile": profile_for_tokens(window.tokens),
                      "limited_by": window.limited_by},
        "budgets": budgets.to_mapping(),
        "bundle_budgets_tokens": {mode: budgets.bundle_tokens(mode) for mode in ("compact", "standard", "full")},
    }


def budget_override(value: Any, *, legacy: str | None = None) -> int | None:
    """An explicit configured value, or ``None`` when unset, invalid or the stored historical default."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or (legacy is not None and number == LEGACY_DEFAULTS.get(legacy)):
        return None
    return number


__all__ = [
    "CALIBRATION_WINDOW", "CUSTOM_PROFILE", "ConfiguredWindow", "ContextBudgets", "DEFAULT_PROFILE",
    "EffectiveWindow", "LEGACY_DEFAULTS", "PROFILES", "ProviderLimitProbe", "SHARES", "budget_override",
    "combine_limits", "configured_window", "context_budgets", "effective_window", "effective_window_tokens",
    "nearest_profile", "normalize_profile", "profile_for_tokens", "provider_limit_probe", "request_overhead_tokens",
    "set_provider_limit_probe",
]
