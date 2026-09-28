"""Hub decision: how to handle a task whose context does not fit the window (LCTX-004).

Planning and delegation are Hub responsibilities (AGENTS.md), so the Hub decides,
per task, between:

- ``fit``        -- one call, the context fits;
- ``compact``    -- slightly too big: condense history/tool output/context (invariants kept);
- ``retrieve``   -- a large corpus, but the task needs only parts: load them selectively;
- ``sequential`` -- everything is needed and order matters: chunk by chunk with a carried state;
- ``map_reduce`` -- everything is needed, parts are independent: parallel Hub subtasks, then merge;
- ``escalate``   -- too large or unclear to handle automatically: approval / a larger model.

Rules decide first (size ratio, kind of input, kind of task). Where the rules are
unsure, the decision provider area ``context_strategy`` may be asked (a typed choice;
off unless configured); otherwise a safe default applies. Every decision is recorded
with its reason. The decision itself does not change execution; strategies act on it
(LCTX-005..008) according to ``context_strategy.mode`` (off / shadow / active).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent.context_window import ContextFit

STRATEGIES = ("fit", "compact", "retrieve", "sequential", "map_reduce", "escalate")
INPUT_KINDS = ("conversation", "corpus", "parts", "ordered", "unknown")
MODES = ("off", "shadow", "active")
DEFAULTS: dict[str, Any] = {
    "mode": "shadow",  # decide and record; strategies act only in "active"
    "compact_max_ratio": 1.5,  # up to 1.5x the budget: condense
    "escalate_min_ratio": 40.0,  # 40x the budget and more: not automatically
    "chunk_fill": 0.6,  # a chunk uses 60% of the budget; the rest is instructions and carried state
    "max_parallel": 4,
    "ask_decision_provider": True,  # only has an effect if decision_providers.areas.context_strategy is on
    # compact/retrieve move the material into a workspace file the worker reads section by section.
    # That needs an iteratively reading worker (tool loop); the autopilot's single-shot flow
    # (one proposal, one execution) cannot, so by default both are carried out as splits.
    "externalize": False,
    # fixed part of every proposal request besides the material: tool definitions, AGENTS.md,
    # system prompt (measured live: ~14k of a 35k request). The material budget excludes it.
    "request_overhead_tokens": 12000,
}
# how compact/retrieve are carried out without externalizing: an ordered pass / a question per part
SPLIT_EQUIVALENTS = {"compact": "sequential", "retrieve": "map_reduce"}
AREA = "context_strategy"
STRATEGY_OPTIONS: dict[str, str] = {
    "compact": "Condense the existing context (history, tool output) a little; nothing essential is lost.",
    "retrieve": "Only some parts of a large body of material are relevant: load those selectively.",
    "sequential": "All material is needed and its order matters: process it piece by piece, carrying notes.",
    "map_reduce": "All material is needed and its parts are independent: process parts in parallel, then merge.",
    "escalate": "Too large or too unclear to handle automatically: ask for approval or a larger model.",
}
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextStrategyRequest:
    fit: ContextFit
    task_kind: str = ""
    input_kind: str = "unknown"  # conversation | corpus | parts | ordered | unknown
    parts: int = 0  # independent parts (files, documents), if known
    question_like: bool = False  # the task asks about the material rather than transforming all of it
    description: str = ""  # short task text, for the decision provider in the grey zone


@dataclass(frozen=True)
class ContextStrategyDecision:
    strategy: str
    reason: str
    decided_by: str  # rules | decision_provider | default
    fit: ContextFit
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {"strategy": self.strategy, "reason": self.reason, "decided_by": self.decided_by,
                "fit": self.fit.to_mapping(), "parameters": dict(self.parameters)}


def normalize_config(raw: Any) -> dict[str, Any]:
    cfg = {**DEFAULTS, **(dict(raw) if isinstance(raw, Mapping) else {})}
    cfg["mode"] = str(cfg.get("mode") or "shadow").strip().lower()
    if cfg["mode"] not in MODES:
        cfg["mode"] = "shadow"
    for key, low, high in (("compact_max_ratio", 1.0, 10.0), ("escalate_min_ratio", 2.0, 1000.0),
                           ("chunk_fill", 0.2, 0.9)):
        try:
            cfg[key] = min(high, max(low, float(cfg[key])))
        except (TypeError, ValueError):
            cfg[key] = DEFAULTS[key]
    cfg["max_parallel"] = max(1, min(32, int(cfg.get("max_parallel") or DEFAULTS["max_parallel"])))
    cfg["ask_decision_provider"] = bool(cfg.get("ask_decision_provider", True))
    cfg["externalize"] = bool(cfg.get("externalize", False))
    try:
        cfg["request_overhead_tokens"] = max(0, min(24000, int(cfg.get("request_overhead_tokens"))))
    except (TypeError, ValueError):
        cfg["request_overhead_tokens"] = DEFAULTS["request_overhead_tokens"]
    return cfg


def _parameters(strategy: str, request: ContextStrategyRequest, cfg: Mapping[str, Any]) -> dict[str, Any]:
    if strategy not in ("sequential", "map_reduce"):
        return {}
    chunk_budget = max(512, int(request.fit.budget_tokens * float(cfg["chunk_fill"])))
    chunks = max(2, math.ceil(request.fit.estimated_tokens / chunk_budget))
    parameters: dict[str, Any] = {"chunk_budget_tokens": chunk_budget, "chunks": chunks}
    if strategy == "map_reduce":
        parameters["parallelism"] = min(int(cfg["max_parallel"]), chunks)
    return parameters


def decide_by_rules(request: ContextStrategyRequest, cfg: Mapping[str, Any]) -> tuple[str, str] | None:
    """``(strategy, reason)`` where the rules are sure; ``None`` for the grey zone."""
    ratio = request.fit.ratio
    if request.fit.fits:
        return "fit", "fits_window"
    if ratio >= float(cfg["escalate_min_ratio"]):
        return "escalate", f"ratio_{ratio:.1f}_beyond_{cfg['escalate_min_ratio']}"
    if request.input_kind == "conversation" or ratio <= float(cfg["compact_max_ratio"]):
        return "compact", f"ratio_{ratio:.2f}" if request.input_kind != "conversation" else "conversation_history"
    if request.input_kind == "corpus" or (request.question_like and request.input_kind != "ordered"):
        return "retrieve", "question_over_large_material"
    if request.input_kind == "parts":
        return "map_reduce", f"independent_parts_{request.parts}"
    if request.input_kind == "ordered":
        return "sequential", "ordered_material"
    return None


class ContextStrategyService:
    def __init__(self, *, config: Mapping[str, Any] | None = None, decision_service: Any = None) -> None:
        self._cfg = normalize_config(config)
        self._decisions = decision_service

    @property
    def mode(self) -> str:
        return self._cfg["mode"]

    @property
    def externalize(self) -> bool:
        return self._cfg["externalize"]

    @property
    def request_overhead_tokens(self) -> int:
        return self._cfg["request_overhead_tokens"]

    def as_split(self, decision: ContextStrategyDecision) -> ContextStrategyDecision:
        """``compact``/``retrieve`` carried out as their split equivalent (no externalizing); others unchanged."""
        strategy = SPLIT_EQUIVALENTS.get(decision.strategy)
        if strategy is None:
            return decision
        request = ContextStrategyRequest(fit=decision.fit)
        return ContextStrategyDecision(strategy, f"{decision.strategy}_as_split:{decision.reason}", decision.decided_by,
                                       decision.fit, _parameters(strategy, request, self._cfg))

    def decide(self, request: ContextStrategyRequest) -> ContextStrategyDecision:
        ruled = decide_by_rules(request, self._cfg)
        if ruled is not None:
            strategy, reason = ruled
            decision = ContextStrategyDecision(strategy, reason, "rules", request.fit,
                                               _parameters(strategy, request, self._cfg))
        else:
            decision = self._grey_zone(request)
        self._record(decision, request)
        return decision

    def _grey_zone(self, request: ContextStrategyRequest) -> ContextStrategyDecision:
        """Unknown input shape: a typed decision if configured, else sequential (processes everything, safely)."""
        if self._cfg["ask_decision_provider"] and request.description.strip():
            chosen = self._ask_decision_provider(request)
            if chosen is not None:
                strategy, confidence = chosen
                return ContextStrategyDecision(strategy, f"decision_provider_confidence_{confidence:.2f}",
                                               "decision_provider", request.fit,
                                               _parameters(strategy, request, self._cfg))
        return ContextStrategyDecision("sequential", "unknown_input_shape_default", "default", request.fit,
                                       _parameters("sequential", request, self._cfg))

    def _ask_decision_provider(self, request: ContextStrategyRequest) -> tuple[str, float] | None:
        try:
            from agent.services.decision_providers.types import DecisionQuestion, DecisionRequest

            service = self._decisions
            if service is None:
                from agent.services.decision_providers.service import get_decision_service

                service = get_decision_service()
            if service.area_mode(AREA) == "off":
                return None
            state = (f"Task ({request.task_kind or 'unknown kind'}): {request.description.strip()[:3000]}\n"
                     f"Material: about {request.fit.estimated_tokens} tokens, {request.fit.ratio:.1f}x the model "
                     f"window; {request.parts or 'unknown number of'} parts.")
            outcome = service.decide(AREA, DecisionRequest(state=state, purpose=AREA, questions=(
                DecisionQuestion.choice("strategy", "How should the task handle material that does not fit the "
                                        "model's context window?", STRATEGY_OPTIONS),)))
            if outcome is None or not outcome.usable:
                return None
            answer = outcome.outcome.result.answer("strategy")
            return answer.choice, answer.confidence
        except Exception:  # noqa: BLE001 -- the grey zone falls back to the safe default
            _log.debug("context strategy decision provider failed", exc_info=True)
            return None

    def _record(self, decision: ContextStrategyDecision, request: ContextStrategyRequest) -> None:
        if decision.strategy == "fit":
            return
        try:
            from agent.metrics import CONTEXT_STRATEGY_DECISIONS_TOTAL

            CONTEXT_STRATEGY_DECISIONS_TOTAL.labels(strategy=decision.strategy, decided_by=decision.decided_by).inc()
            _log.info("context strategy %s (%s, %s) for %s input, %s", decision.strategy, decision.reason,
                      decision.decided_by, request.input_kind, decision.fit.to_mapping())
        except Exception:  # noqa: BLE001 -- recording never changes a decision
            pass


def get_context_strategy_service(config: Mapping[str, Any] | None = None) -> ContextStrategyService:
    if config is None:
        try:
            from flask import current_app, has_app_context

            config = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("context_strategy") \
                if has_app_context() else None
        except Exception:  # noqa: BLE001
            config = None
    return ContextStrategyService(config=config)
