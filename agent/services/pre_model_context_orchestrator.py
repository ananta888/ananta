"""APMCO-002: PreModelContextOrchestrator — central, optional pre-model context service.

Default is fully backward-compatible: when ``pre_model_context.enabled`` is
``false`` (the default), nothing runs and all existing flows continue unchanged.

Architecture
────────────
1. ``orchestrate()`` is the single entry point.
2. It resolves the effective mode for the requested surface.
3. Depending on the mode it runs zero or more of:
   - Task classification (heuristic, no LLM)
   - Context retrieval (via injected callables)
   - Candidate ranking (CandidateScorer)
   - No-LLM decision (DeterministicDecisionEngine)
   - Cache get/put (ContextPackageCache)
4. It builds a ``PreModelTrace`` that is attached to the result.
5. At no point does it modify an existing route, worker or adapter directly —
   callers opt in by checking ``result.decision``.

Injected callables
──────────────────
``retrieve_fn``  : (task_text, domain_hint, workspace_dir, budget) → list[dict]
    Must return raw candidate dicts compatible with CandidateScorer.
    Defaults to a no-op stub (returns []) so the service is always testable.
``index_status_fn`` : () → dict
    Returns current RAG/CodeCompass index status.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.services.pre_model_context_cache import (
    ContextPackageCache,
    build_cache_key,
    hash_config,
    hash_task,
)
from agent.services.pre_model_context_config import (
    MODE_CONTEXT_FIRST,
    MODE_DETERMINISTIC_ONLY,
    MODE_DISABLED,
    MODE_OBSERVE_ONLY,
    MODE_PREFER_CONTEXT,
    MODE_PREFER_DETERMINISTIC,
    MODE_WORKER_DECIDES,
    PreModelContextConfig,
    classify_task,
)
from agent.services.pre_model_context_decision import (
    ANSWER_CANNOT,
    ANSWER_NEEDS_LLM,
    DeterministicDecisionEngine,
)
from agent.services.pre_model_context_ranking import CandidateScorer, ScoredCandidate

log = logging.getLogger(__name__)

# ── Decision constants ────────────────────────────────────────────────────────
DECISION_PASS_THROUGH = "pass_through"      # disabled / observe_only: call original flow
DECISION_WORKER_DECIDES = "worker_decides"  # give worker tool catalog, no preflight
DECISION_USE_CONTEXT = "use_context"        # ContextPackage ready for prompt
DECISION_DETERMINISTIC = "deterministic"    # no LLM needed
DECISION_CANNOT_ANSWER = "cannot_answer"    # deterministic_only + no evidence


# ── Data contracts ────────────────────────────────────────────────────────────

@dataclass
class ContextPackage:
    schema: str = "context_package.v1"
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    surface: str = ""
    mode: str = ""
    task_kind: str = ""
    candidates: list[ScoredCandidate] = field(default_factory=list)
    denied_candidates: list[ScoredCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    budget_remaining_chars: int = 0
    has_sensitive_content: bool = False
    cache_status: str = "miss"       # hit / miss / stale / disabled
    from_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "trace_id": self.trace_id,
            "surface": self.surface,
            "mode": self.mode,
            "task_kind": self.task_kind,
            "candidates": [_candidate_dict(c) for c in self.candidates],
            "denied_candidates": [_candidate_dict(c) for c in self.denied_candidates],
            "warnings": self.warnings,
            "budget_remaining_chars": self.budget_remaining_chars,
            "has_sensitive_content": self.has_sensitive_content,
            "cache_status": self.cache_status,
            "from_cache": self.from_cache,
        }


@dataclass
class TraceEvent:
    event: str
    ts: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreModelTrace:
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    surface: str = ""
    mode: str = ""
    config_resolved: bool = False
    task_kind: str = ""
    retrieval_count: int = 0
    ranked_count: int = 0
    denied_count: int = 0
    cache_status: str = "disabled"
    decision: str = ""
    fallback_used: bool = False
    warnings: list[str] = field(default_factory=list)
    events: list[TraceEvent] = field(default_factory=list)
    duration_ms: float = 0.0

    def add(self, event: str, **data: Any) -> None:
        self.events.append(TraceEvent(event=event, data=data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "surface": self.surface,
            "mode": self.mode,
            "config_resolved": self.config_resolved,
            "task_kind": self.task_kind,
            "retrieval_count": self.retrieval_count,
            "ranked_count": self.ranked_count,
            "denied_count": self.denied_count,
            "cache_status": self.cache_status,
            "decision": self.decision,
            "fallback_used": self.fallback_used,
            "warnings": self.warnings,
            "duration_ms": round(self.duration_ms, 2),
            "events": [{"event": e.event, "ts": e.ts, "data": e.data} for e in self.events],
        }


@dataclass
class OrchestratorResult:
    """Returned by PreModelContextOrchestrator.orchestrate()."""
    decision: str = DECISION_PASS_THROUGH
    context_package: ContextPackage | None = None
    deterministic_answer: dict[str, Any] | None = None
    trace: PreModelTrace | None = None
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def has_context(self) -> bool:
        return self.context_package is not None and bool(self.context_package.candidates)

    @property
    def should_call_llm(self) -> bool:
        return self.decision in (
            DECISION_PASS_THROUGH,
            DECISION_WORKER_DECIDES,
            DECISION_USE_CONTEXT,
        )


# ── Default stubs (no-op, always testable) ────────────────────────────────────

def _noop_retrieve(task_text: str, domain_hint: str, workspace_dir: str, budget: int) -> list[dict[str, Any]]:
    return []


def _noop_index_status() -> dict[str, Any]:
    return {"status": "unknown", "manifest_hash": ""}


# ── Main service ──────────────────────────────────────────────────────────────

class PreModelContextOrchestrator:
    """Central, optional pre-model context orchestration service (APMCO).

    All external dependencies are injected so the service is fully testable
    without live CodeCompass / RAG / LLM infrastructure.
    """

    def __init__(
        self,
        *,
        retrieve_fn: Callable[[str, str, str, int], list[dict[str, Any]]] | None = None,
        index_status_fn: Callable[[], dict[str, Any]] | None = None,
        cache: ContextPackageCache | None = None,
    ) -> None:
        self._retrieve = retrieve_fn or _noop_retrieve
        self._index_status = index_status_fn or _noop_index_status
        self._cache = cache

    def orchestrate(
        self,
        *,
        surface: str = "",
        backend: str = "",
        task_text: str = "",
        task_kind: str | None = None,
        working_files: list[str] | None = None,
        domain_hint: str = "",
        workspace_dir: str = "",
        user_config: dict[str, Any] | None = None,
        budget_chars: int = 0,
        repo_commit: str = "",
    ) -> OrchestratorResult:
        """Main entry point.

        Returns an ``OrchestratorResult`` — never raises. On any internal
        error the result carries ``decision=DECISION_PASS_THROUGH`` so existing
        flows continue unchanged.
        """
        _t0 = time.time()
        trace = PreModelTrace(surface=surface)
        try:
            return self._run(
                surface=surface,
                backend=backend,
                task_text=task_text,
                task_kind=task_kind,
                working_files=working_files or [],
                domain_hint=domain_hint,
                workspace_dir=workspace_dir,
                user_config=user_config,
                budget_chars=budget_chars,
                repo_commit=repo_commit,
                trace=trace,
                t0=_t0,
            )
        except Exception as exc:
            log.warning("PreModelContextOrchestrator error: %s", exc, exc_info=True)
            trace.add("error", message=str(exc))
            trace.duration_ms = (time.time() - _t0) * 1000
            return OrchestratorResult(
                decision=DECISION_PASS_THROUGH,
                trace=trace,
                warnings=["orchestrator_error"],
                error=str(exc),
            )

    def _run(
        self,
        *,
        surface: str,
        backend: str,
        task_text: str,
        task_kind: str | None,
        working_files: list[str],
        domain_hint: str,
        workspace_dir: str,
        user_config: dict[str, Any] | None,
        budget_chars: int,
        repo_commit: str,
        trace: PreModelTrace,
        t0: float,
    ) -> OrchestratorResult:
        # ── 1. Config resolution ──────────────────────────────────────────────
        cfg = PreModelContextConfig.from_raw(user_config)
        effective_mode = cfg.resolve_surface_mode(surface)
        trace.mode = effective_mode
        trace.config_resolved = True
        trace.add("config_resolved", mode=effective_mode, surface=surface)

        # ── 2. Short-circuit: disabled ────────────────────────────────────────
        if effective_mode == MODE_DISABLED:
            trace.decision = DECISION_PASS_THROUGH
            trace.duration_ms = (time.time() - t0) * 1000
            return OrchestratorResult(decision=DECISION_PASS_THROUGH, trace=trace)

        # ── 3. Task classification ────────────────────────────────────────────
        kind = classify_task(task_text, task_kind)
        trace.task_kind = kind
        trace.add("task_classified", kind=kind)

        # ── 4. Worker-decides mode ────────────────────────────────────────────
        if effective_mode == MODE_WORKER_DECIDES:
            trace.decision = DECISION_WORKER_DECIDES
            trace.duration_ms = (time.time() - t0) * 1000
            return OrchestratorResult(
                decision=DECISION_WORKER_DECIDES,
                trace=trace,
            )

        # ── 5. Observe-only: compute context but don't inject ─────────────────
        if effective_mode == MODE_OBSERVE_ONLY:
            budget = budget_chars or cfg.context_budget_chars
            candidates, pkg = self._build_context(
                task_text=task_text,
                kind=kind,
                working_files=working_files,
                domain_hint=domain_hint,
                workspace_dir=workspace_dir,
                budget=budget,
                surface=surface,
                mode=effective_mode,
                trace=trace,
            )
            trace.decision = DECISION_PASS_THROUGH
            trace.duration_ms = (time.time() - t0) * 1000
            return OrchestratorResult(
                decision=DECISION_PASS_THROUGH,
                context_package=pkg,
                trace=trace,
                warnings=pkg.warnings if pkg else [],
            )

        # ── 6. Context or deterministic modes ─────────────────────────────────
        budget = budget_chars or cfg.context_budget_chars
        idx_status = self._index_status()
        manifest_hash = str(idx_status.get("manifest_hash") or "")

        # Cache lookup
        cache_key = build_cache_key(
            repo_commit=repo_commit,
            manifest_hash=manifest_hash,
            task_hash=hash_task(task_text),
            working_files=working_files,
            config_hash=hash_config(user_config),
            mode=effective_mode,
            surface=surface,
        )
        cached_payload: dict[str, Any] | None = None
        if cfg.cache_enabled and self._cache:
            cached_payload = self._cache.get(cache_key, manifest_hash=manifest_hash)
            if cached_payload:
                trace.cache_status = "hit"
                trace.add("cache_hit", key=cache_key[:16])

        trace.cache_status = "hit" if cached_payload else "miss"

        if cached_payload:
            pkg = _pkg_from_dict(cached_payload)
            pkg.from_cache = True
            pkg.cache_status = "hit"
        else:
            trace.add("retrieval_started")
            candidates, pkg = self._build_context(
                task_text=task_text,
                kind=kind,
                working_files=working_files,
                domain_hint=domain_hint,
                workspace_dir=workspace_dir,
                budget=budget,
                surface=surface,
                mode=effective_mode,
                trace=trace,
            )
            # Cache store
            if cfg.cache_enabled and self._cache and pkg:
                self._cache.put(
                    cache_key,
                    pkg.to_dict(),
                    manifest_hash=manifest_hash,
                    allow_sensitive=not pkg.has_sensitive_content,
                )

        # ── 7. Deterministic decision ─────────────────────────────────────────
        det_only = effective_mode == MODE_DETERMINISTIC_ONLY
        prefer_det = effective_mode == MODE_PREFER_DETERMINISTIC
        if det_only or prefer_det:
            engine = DeterministicDecisionEngine(
                allow_no_llm_answers=cfg.allow_no_llm_answers,
                deterministic_only=det_only,
            )
            raw_candidates = [_candidate_raw(c) for c in (pkg.candidates if pkg else [])]
            det_answer = engine.decide(
                task_text=task_text,
                task_kind=kind,
                candidates=raw_candidates,
                index_status=idx_status,
            )
            trace.add("decision_engine", answer_type=det_answer.answer_type)

            if det_answer.answer_type == ANSWER_CANNOT:
                trace.decision = DECISION_CANNOT_ANSWER
                trace.warnings.extend(det_answer.warnings)
                trace.duration_ms = (time.time() - t0) * 1000
                return OrchestratorResult(
                    decision=DECISION_CANNOT_ANSWER,
                    context_package=pkg,
                    deterministic_answer=det_answer.to_dict(),
                    trace=trace,
                    warnings=det_answer.warnings,
                )

            if det_answer.answer_type != ANSWER_NEEDS_LLM:
                trace.decision = DECISION_DETERMINISTIC
                trace.warnings.extend(det_answer.warnings)
                trace.duration_ms = (time.time() - t0) * 1000
                return OrchestratorResult(
                    decision=DECISION_DETERMINISTIC,
                    context_package=pkg,
                    deterministic_answer=det_answer.to_dict(),
                    trace=trace,
                    warnings=det_answer.warnings,
                )

        # ── 8. Context-bearing modes ──────────────────────────────────────────
        decision = (
            DECISION_USE_CONTEXT
            if pkg and pkg.candidates
            else DECISION_PASS_THROUGH
        )
        if decision == DECISION_PASS_THROUGH and effective_mode in (
            MODE_PREFER_CONTEXT, MODE_CONTEXT_FIRST
        ):
            trace.fallback_used = True
            trace.warnings.append("context_build_fallback")

        trace.decision = decision
        trace.duration_ms = (time.time() - t0) * 1000
        combined_warnings = list(dict.fromkeys(
            (pkg.warnings if pkg else []) + trace.warnings
        ))
        return OrchestratorResult(
            decision=decision,
            context_package=pkg,
            trace=trace,
            warnings=combined_warnings,
        )

    def _build_context(
        self,
        *,
        task_text: str,
        kind: str,
        working_files: list[str],
        domain_hint: str,
        workspace_dir: str,
        budget: int,
        surface: str,
        mode: str,
        trace: PreModelTrace,
    ) -> tuple[list[ScoredCandidate], ContextPackage | None]:
        try:
            raw = self._retrieve(task_text, domain_hint, workspace_dir, budget)
            trace.retrieval_count = len(raw)
            trace.add("retrieval_finished", count=len(raw))
        except Exception as exc:
            log.warning("PreModelContextOrchestrator: retrieval failed: %s", exc)
            trace.add("retrieval_error", message=str(exc))
            trace.warnings.append("retrieval_error")
            return [], None

        scorer = CandidateScorer(working_files=working_files)
        scored = scorer.score_all(raw)
        denied = [c for c in scored if c.policy_denied]
        allowed = [c for c in scored if not c.policy_denied]
        trace.ranked_count = len(scored)
        trace.denied_count = len(denied)
        trace.add("ranked", total=len(scored), denied=len(denied))

        has_sensitive = any(c.sensitivity_class == "high" for c in allowed)
        pkg = ContextPackage(
            surface=surface,
            mode=mode,
            task_kind=kind,
            candidates=allowed,
            denied_candidates=denied,
            warnings=trace.warnings[:],
            budget_remaining_chars=budget,
            has_sensitive_content=has_sensitive,
            cache_status="miss",
        )
        return allowed, pkg


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candidate_dict(c: ScoredCandidate) -> dict[str, Any]:
    return {
        "path": c.path,
        "record_id": c.record_id,
        "excerpt": c.excerpt,
        "symbols": c.symbols,
        "embedding_score": c.embedding_score,
        "symbol_match_score": c.symbol_match_score,
        "graph_distance_score": c.graph_distance_score,
        "working_file_bonus": c.working_file_bonus,
        "domain_scope_bonus": c.domain_scope_bonus,
        "test_relation_bonus": c.test_relation_bonus,
        "recency_bonus": c.recency_bonus,
        "policy_penalty": c.policy_penalty,
        "sensitivity_penalty": c.sensitivity_penalty,
        "final_score": c.final_score,
        "policy_denied": c.policy_denied,
        "reason": c.reason,
        "domain": c.domain,
        "sensitivity_class": c.sensitivity_class,
        "graph_edges": c.graph_edges,
    }


def _candidate_raw(c: ScoredCandidate) -> dict[str, Any]:
    return {"path": c.path, "record_id": c.record_id, "embedding_score": c.embedding_score}


def _pkg_from_dict(d: dict[str, Any]) -> ContextPackage:
    from agent.services.pre_model_context_ranking import ScoredCandidate
    pkg = ContextPackage(
        surface=str(d.get("surface") or ""),
        mode=str(d.get("mode") or ""),
        task_kind=str(d.get("task_kind") or ""),
        warnings=list(d.get("warnings") or []),
        budget_remaining_chars=int(d.get("budget_remaining_chars") or 0),
        has_sensitive_content=bool(d.get("has_sensitive_content")),
        cache_status="hit",
    )
    for cdict in (d.get("candidates") or []):
        pkg.candidates.append(ScoredCandidate(**{
            k: cdict[k] for k in ScoredCandidate.__dataclass_fields__ if k in cdict
        }))
    return pkg


# ── Module-level singleton ────────────────────────────────────────────────────

_orchestrator: PreModelContextOrchestrator | None = None


def get_pre_model_context_orchestrator() -> PreModelContextOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PreModelContextOrchestrator()
    return _orchestrator
