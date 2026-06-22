"""AgentFeatureProvider — AGENT-FEATURE-001 through AGENT-FEATURE-004.

Architecture principle
----------------------
    "Agents deliver structured feature signals.
     Ananta controls policy, scope, encoding, trace, deterministic decisions."

Agents produce ``AgentFeatureSignal`` records.  Ananta's
``AgentFeatureOrchestrator`` decides which providers are allowed, collects
signals, applies policy, and converts them into embedding-compatible records.
Agents NEVER make routing or ordering decisions.

AGENT-FEATURE-001  Data structures: AgentFeatureSignal, AgentFeaturePolicy
AGENT-FEATURE-002  Adapter stubs (OpenCode, Codex, Claude-CLI)
AGENT-FEATURE-003  AgentFeatureProvider Protocol + StubAgentFeatureProvider
AGENT-FEATURE-004  AgentFeatureOrchestrator
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AGENT-FEATURE-001: Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentFeatureSignal:
    """Structured feature signal produced by a single agent provider.

    All fields are typed and bounded.  ``feature_text`` is a short structured
    description — NOT free prose.  ``feature_vector`` may be empty; consumers
    must handle that case.

    Attributes
    ----------
    provider_id:
        Stable string identifier for the provider, e.g.
        ``"opencode-analyzer"``, ``"codex-classifier"``, ``"claude-cli-doc"``.
    model_or_agent_version:
        Version string of the underlying model or agent.
    feature_text:
        Short structured description of the feature.  Must not be free prose.
        Example: ``"symbol:MyClass type:class visibility:public"``.
    feature_vector:
        Optional embedding vector produced by the agent.  May be ``[]``.
    confidence:
        Estimated confidence in the range ``[0.0, 1.0]``.
    evidence_refs:
        List of file paths or record IDs that support the signal.
    input_hash:
        SHA-256 hex digest (first 24 chars) of the serialised input package.
    output_hash:
        SHA-256 hex digest (first 24 chars) of ``feature_text``.
    elapsed_ms:
        Wall-clock time in milliseconds for the provider call.
    source_scope:
        Always ``"agent_feature"``.
    policy_decision:
        One of ``"allowed"`` | ``"blocked"`` | ``"degraded"``.
    """

    provider_id: str
    model_or_agent_version: str
    feature_text: str
    feature_vector: list[float]
    confidence: float
    evidence_refs: list[str]
    input_hash: str
    output_hash: str
    elapsed_ms: float
    source_scope: str  # always "agent_feature"
    policy_decision: str  # "allowed" | "blocked" | "degraded"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_or_agent_version": self.model_or_agent_version,
            "feature_text": self.feature_text,
            "feature_vector": list(self.feature_vector),
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "source_scope": self.source_scope,
            "policy_decision": self.policy_decision,
        }


@dataclass(frozen=True)
class AgentFeaturePolicy:
    """Policy governing which agent providers are permitted to run.

    Attributes
    ----------
    enabled:
        Master switch.  If ``False``, the orchestrator returns ``[]`` without
        calling any provider.
    external_calls_allowed:
        If ``False``, providers that require external network calls must
        treat themselves as blocked.  (Enforcement is per-adapter.)
    allowed_provider_ids:
        Frozenset of provider IDs that are permitted to run.  An empty set
        means all providers are blocked (unless ``enabled`` is also False).
    no_write_mode:
        If ``True``, providers MUST NOT modify any file or state.
    max_input_chars:
        Maximum number of characters in the serialised ``context_package``
        passed to a provider.
    allowed_paths:
        Optional list of path prefixes that providers are allowed to read.
        Empty list means no path restriction is enforced.
    """

    enabled: bool = False
    external_calls_allowed: bool = False
    allowed_provider_ids: frozenset[str] = field(default_factory=frozenset)
    no_write_mode: bool = True
    max_input_chars: int = 4000
    allowed_paths: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AGENT-FEATURE-003: AgentFeatureProvider Protocol
# ---------------------------------------------------------------------------

class AgentFeatureProvider(Protocol):
    """Protocol for all agent feature providers.

    Implementors must be stateless or at most accumulate read-only diagnostics.
    All writes are prohibited when ``policy.no_write_mode`` is True.
    """

    provider_id: str

    def analyze(
        self,
        *,
        context_package: dict[str, Any],
        query: str,
        policy: AgentFeaturePolicy,
    ) -> AgentFeatureSignal:
        """Produce a structured feature signal for the given context.

        Parameters
        ----------
        context_package:
            Structured context — never the full repository.  Should contain
            only what is needed for the feature extraction (symbol stubs,
            relevant excerpts, dependency graph fragment, …).
        query:
            The original user query or task description.
        policy:
            Runtime policy.  The provider MUST check ``policy.no_write_mode``
            and ``policy.external_calls_allowed`` before taking any I/O action.

        Returns
        -------
        AgentFeatureSignal
            A structured, bounded feature signal.
        """
        ...


# ---------------------------------------------------------------------------
# Stub provider (AGENT-FEATURE-003)
# ---------------------------------------------------------------------------

class StubAgentFeatureProvider:
    """No-op provider that always returns a blocked signal.

    Used as the only registered provider in ``AgentFeatureOrchestrator``
    when production adapters are not yet implemented (AGENT-FEATURE-002
    stubs are not signed off).  Safe to use in all environments.
    """

    provider_id: str = "stub-no-op"

    def analyze(
        self,
        *,
        context_package: dict[str, Any],  # noqa: ARG002
        query: str,
        policy: AgentFeaturePolicy,  # noqa: ARG002
    ) -> AgentFeatureSignal:
        """Return a blocked signal immediately — never calls any external service."""
        input_hash = _hash_dict({"query": query})
        return AgentFeatureSignal(
            provider_id=self.provider_id,
            model_or_agent_version="stub-v0",
            feature_text="",
            feature_vector=[],
            confidence=0.0,
            evidence_refs=[],
            input_hash=input_hash,
            output_hash=_hash_str(""),
            elapsed_ms=0.0,
            source_scope="agent_feature",
            policy_decision="blocked",
        )

    def __repr__(self) -> str:
        return "StubAgentFeatureProvider()"


# ---------------------------------------------------------------------------
# AGENT-FEATURE-002: Adapter stubs
# ---------------------------------------------------------------------------

_ADAPTER_NOT_IMPLEMENTED_MSG = (
    "AGENT-FEATURE-002: adapter not yet implemented — see "
    "todos/todo.codecompass-vector-encoding-agent-orchestration-delta-2026-06-22.json"
)


class OpenCodeFeatureAdapter:
    """Feature adapter for the OpenCode runtime agent (AGENT-FEATURE-002 stub).

    Raises ``NotImplementedError`` until the adapter is implemented.
    """

    provider_id: str = "opencode-analyzer"

    def analyze(
        self,
        *,
        context_package: dict[str, Any],  # noqa: ARG002
        query: str,  # noqa: ARG002
        policy: AgentFeaturePolicy,  # noqa: ARG002
    ) -> AgentFeatureSignal:
        raise NotImplementedError(_ADAPTER_NOT_IMPLEMENTED_MSG)

    def __repr__(self) -> str:
        return "OpenCodeFeatureAdapter(NOT_IMPLEMENTED)"


class CodexFeatureAdapter:
    """Feature adapter for the Codex model agent (AGENT-FEATURE-002 stub).

    Raises ``NotImplementedError`` until the adapter is implemented.
    """

    provider_id: str = "codex-classifier"

    def analyze(
        self,
        *,
        context_package: dict[str, Any],  # noqa: ARG002
        query: str,  # noqa: ARG002
        policy: AgentFeaturePolicy,  # noqa: ARG002
    ) -> AgentFeatureSignal:
        raise NotImplementedError(_ADAPTER_NOT_IMPLEMENTED_MSG)

    def __repr__(self) -> str:
        return "CodexFeatureAdapter(NOT_IMPLEMENTED)"


class ClaudeCliFeatureAdapter:
    """Feature adapter for the Claude CLI documentation agent (AGENT-FEATURE-002 stub).

    Raises ``NotImplementedError`` until the adapter is implemented.
    """

    provider_id: str = "claude-cli-doc"

    def analyze(
        self,
        *,
        context_package: dict[str, Any],  # noqa: ARG002
        query: str,  # noqa: ARG002
        policy: AgentFeaturePolicy,  # noqa: ARG002
    ) -> AgentFeatureSignal:
        raise NotImplementedError(_ADAPTER_NOT_IMPLEMENTED_MSG)

    def __repr__(self) -> str:
        return "ClaudeCliFeatureAdapter(NOT_IMPLEMENTED)"


# ---------------------------------------------------------------------------
# AGENT-FEATURE-004: AgentFeatureOrchestrator
# ---------------------------------------------------------------------------

class AgentFeatureOrchestrator:
    """Collect, gate, and convert agent feature signals.

    Ananta controls all policy here — providers only produce signals.

    Parameters
    ----------
    policy:
        Runtime policy governing which providers are allowed to run and
        what they are permitted to do.
    providers:
        List of registered providers.  The orchestrator will only call those
        whose ``provider_id`` appears in ``policy.allowed_provider_ids``.
    """

    def __init__(
        self,
        policy: AgentFeaturePolicy,
        providers: list[Any],  # list[AgentFeatureProvider]
    ) -> None:
        self._policy = policy
        self._providers = list(providers)

    @property
    def policy(self) -> AgentFeaturePolicy:
        return self._policy

    # ------------------------------------------------------------------
    # collect_signals
    # ------------------------------------------------------------------

    def collect_signals(
        self,
        context_package: dict[str, Any],
        query: str,
    ) -> list[AgentFeatureSignal]:
        """Run all allowed providers and collect their signals.

        Parameters
        ----------
        context_package:
            Structured context passed to each provider.  The orchestrator
            enforces ``policy.max_input_chars`` by truncating the serialised
            representation before passing it on.
        query:
            The search query or task description.

        Returns
        -------
        list[AgentFeatureSignal]
            All signals, including degraded ones.  Empty if policy disabled.
        """
        if not self._policy.enabled:
            return []

        # Enforce input size limit: build a bounded copy of the context.
        safe_context = _truncate_context(context_package, self._policy.max_input_chars)

        signals: list[AgentFeatureSignal] = []
        for provider in self._providers:
            pid = str(getattr(provider, "provider_id", ""))
            if pid not in self._policy.allowed_provider_ids:
                log.debug(
                    "AgentFeatureOrchestrator: provider %r not in allowed_provider_ids, skipping",
                    pid,
                )
                continue

            t0 = time.time()
            try:
                signal = provider.analyze(
                    context_package=safe_context,
                    query=query,
                    policy=self._policy,
                )
                signals.append(signal)
            except NotImplementedError as exc:
                elapsed_ms = (time.time() - t0) * 1000.0
                log.warning(
                    "AgentFeatureOrchestrator: provider %r not implemented — degraded. exc=%s",
                    pid,
                    exc,
                )
                signals.append(
                    _degraded_signal(pid, query, safe_context, elapsed_ms, str(exc))
                )
            except Exception as exc:  # noqa: BLE001
                elapsed_ms = (time.time() - t0) * 1000.0
                log.error(
                    "AgentFeatureOrchestrator: provider %r raised unexpected error — degraded. exc=%s",
                    pid,
                    exc,
                )
                signals.append(
                    _degraded_signal(pid, query, safe_context, elapsed_ms, str(exc))
                )

        return signals

    # ------------------------------------------------------------------
    # to_embedding_records
    # ------------------------------------------------------------------

    def to_embedding_records(
        self, signals: list[AgentFeatureSignal]
    ) -> list[dict[str, Any]]:
        """Convert allowed signals to CodeCompass embedding-compatible records.

        Only signals with ``policy_decision == "allowed"`` are included.

        Each record has:
        - ``source_scope: "agent_feature"``
        - ``profile_name: "agent_feature_v1"``
        - ``kind: "agent_signal"``
        - All scalar fields from the signal
        - ``embedding`` set to ``feature_vector`` (may be empty list)

        Parameters
        ----------
        signals:
            Signals returned by ``collect_signals()``.

        Returns
        -------
        list[dict]
            Embedding-compatible records.
        """
        records: list[dict[str, Any]] = []
        for signal in signals:
            if signal.policy_decision != "allowed":
                continue
            records.append({
                "source_scope": "agent_feature",
                "profile_name": "agent_feature_v1",
                "kind": "agent_signal",
                "provider_id": signal.provider_id,
                "model_or_agent_version": signal.model_or_agent_version,
                "feature_text": signal.feature_text,
                "embedding": list(signal.feature_vector),
                "confidence": signal.confidence,
                "evidence_refs": list(signal.evidence_refs),
                "input_hash": signal.input_hash,
                "output_hash": signal.output_hash,
                "elapsed_ms": round(signal.elapsed_ms, 3),
                "policy_decision": signal.policy_decision,
            })
        return records

    # ------------------------------------------------------------------
    # from_config factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any] | None = None,
    ) -> "AgentFeatureOrchestrator":
        """Build an orchestrator from config dict and environment variables.

        Environment variables
        ---------------------
        CODECOMPASS_AGENT_FEATURE_ENABLED
            ``"1"`` to enable.  Default: ``"0"`` (disabled).
        CODECOMPASS_AGENT_FEATURE_EXTERNAL_CALLS_ALLOWED
            ``"1"`` to allow external calls.  Default: ``"0"``.
        CODECOMPASS_AGENT_FEATURE_ALLOWED_PROVIDER_IDS
            Comma-separated provider IDs.  Default: ``""`` (none allowed).

        Config dict keys take precedence over env vars.

        Note: production adapters (AGENT-FEATURE-002) are NOT registered here.
        Only ``StubAgentFeatureProvider`` is registered until the adapters are
        implemented and signed off.
        """
        cfg = dict(config or {})
        env = os.environ

        enabled = _bool(
            cfg.get("enabled", env.get("CODECOMPASS_AGENT_FEATURE_ENABLED", "0"))
        )
        external_calls_allowed = _bool(
            cfg.get(
                "external_calls_allowed",
                env.get("CODECOMPASS_AGENT_FEATURE_EXTERNAL_CALLS_ALLOWED", "0"),
            )
        )
        allowed_ids_raw = str(
            cfg.get("allowed_provider_ids")
            or env.get("CODECOMPASS_AGENT_FEATURE_ALLOWED_PROVIDER_IDS")
            or ""
        )
        allowed_provider_ids: frozenset[str] = frozenset(
            pid.strip() for pid in allowed_ids_raw.split(",") if pid.strip()
        )
        max_input_chars = int(
            cfg.get("max_input_chars")
            or env.get("CODECOMPASS_AGENT_FEATURE_MAX_INPUT_CHARS")
            or 4000
        )
        no_write_mode = _bool(
            cfg.get("no_write_mode", env.get("CODECOMPASS_AGENT_FEATURE_NO_WRITE_MODE", "1"))
        )
        allowed_paths_raw = str(
            cfg.get("allowed_paths")
            or env.get("CODECOMPASS_AGENT_FEATURE_ALLOWED_PATHS")
            or ""
        )
        allowed_paths = [p.strip() for p in allowed_paths_raw.split(",") if p.strip()]

        policy = AgentFeaturePolicy(
            enabled=enabled,
            external_calls_allowed=external_calls_allowed,
            allowed_provider_ids=allowed_provider_ids,
            no_write_mode=no_write_mode,
            max_input_chars=max_input_chars,
            allowed_paths=allowed_paths,
        )

        # Production adapters are AGENT-FEATURE-002 stubs — not registered.
        providers: list[Any] = [StubAgentFeatureProvider()]

        return cls(policy=policy, providers=providers)

    def __repr__(self) -> str:
        return (
            f"AgentFeatureOrchestrator("
            f"enabled={self._policy.enabled}, "
            f"providers={[getattr(p, 'provider_id', '?') for p in self._providers]})"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hash_str(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _hash_dict(d: dict[str, Any]) -> str:
    import json
    serialised = json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()[:24]


def _truncate_context(context: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Return a copy of context with oversized string values truncated."""
    import json
    try:
        raw = json.dumps(context, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        return {}
    if len(raw) <= max_chars:
        return dict(context)
    # Truncate: pass the raw string capped at max_chars as a single key.
    return {"_truncated_context": raw[:max_chars], "_truncated": True}


def _degraded_signal(
    provider_id: str,
    query: str,
    context_package: dict[str, Any],
    elapsed_ms: float,
    error_msg: str,
) -> AgentFeatureSignal:
    input_hash = _hash_dict({"provider_id": provider_id, "query": query})
    return AgentFeatureSignal(
        provider_id=provider_id,
        model_or_agent_version="degraded",
        feature_text=f"degraded:{error_msg[:120]}",
        feature_vector=[],
        confidence=0.0,
        evidence_refs=[],
        input_hash=input_hash,
        output_hash=_hash_str("degraded"),
        elapsed_ms=elapsed_ms,
        source_scope="agent_feature",
        policy_decision="degraded",
    )


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
