"""SnakeDecisionManager — central decision coordinator for Snake surfaces.

Wires together:
  HeuristicRegistry → active candidates
  LeaseReevaluationService → TTL / extend / switch logic
  RuleChain (snake_rules) → fine-grained rule evaluation
  SnakeStateMachine → lifecycle state tracking
  DecisionTrace → per-decision audit record

AI/worker integration:
  Worker can signal ai_response_received or ai_timeout via send_ai_event().
  Default AI timeout = 2.5 s (mirrors ai_snake_worker_client.py).
  When waiting_ai TTL lapses → state machine → fallback_active.

TUI fallback commands:
  FollowWithDistanceCommand — deterministic follow without LLM
  LurkNearCommand          — deterministic lurk without LLM
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from agent.db_models import HeuristicDecisionLeaseDB
from agent.repositories.decision_trace_repo import DecisionTraceRepository
from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository
from agent.services.heuristic_runtime.chain import RuleChain
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.decision_trace import DecisionTrace, DecisionMetricsAccumulator
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicRegistry, get_heuristic_registry
from agent.services.heuristic_runtime.lease_reevaluation_service import LeaseReevaluationService, ReevalOutcome
from agent.services.heuristic_runtime.snake_rules import build_snake_rule_chain
from agent.services.heuristic_runtime.state_machine import SnakeStateMachine, FallbackActiveState

_AI_TIMEOUT_SECONDS = 2.5


# ── Fallback commands ─────────────────────────────────────────────────────────

@dataclass
class FollowWithDistanceCommand:
    """Deterministic follow: move toward active artifact, maintain configured distance."""
    dx: int = 1
    dy: int = 0
    follow_distance: int = 4
    strategy_id: str = "follow_with_distance"

    def to_decision_result(self) -> DecisionResult:
        return DecisionResult.heuristic_follow(
            dx=self.dx, dy=self.dy, strategy_id=self.strategy_id
        )


@dataclass
class LurkNearCommand:
    """Deterministic lurk: stay near last known artifact, patrol slowly."""
    lurk_zone_radius: int = 3
    strategy_id: str = "lurk_near"

    def to_decision_result(self) -> DecisionResult:
        return DecisionResult.heuristic_lurk(strategy_id=self.strategy_id)


# ── Manager ───────────────────────────────────────────────────────────────────

class SnakeDecisionManager:
    def __init__(
        self,
        registry: HeuristicRegistry | None = None,
        lease_repo: HeuristicLeaseRepository | None = None,
        trace_repo: DecisionTraceRepository | None = None,
        rule_chain: RuleChain | None = None,
    ) -> None:
        self._registry = registry or get_heuristic_registry()
        self._lease_repo = lease_repo or HeuristicLeaseRepository()
        self._trace_repo = trace_repo or DecisionTraceRepository()
        self._reeval = LeaseReevaluationService(repo=self._lease_repo, registry=self._registry)
        self._chain = rule_chain or build_snake_rule_chain()
        self._state_machine = SnakeStateMachine()
        self._metrics = DecisionMetricsAccumulator()
        self._last_ai_request_at: float | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def decide(self, ctx: DecisionContext) -> DecisionResult:
        """Main entry point. Returns a DecisionResult for the current context."""
        trace = DecisionTrace(
            surface=ctx.source_surface,
            context_hash=ctx.context_hash,
            started_at=time.time(),
        )

        # Check for AI timeout in waiting_ai state
        self._tick_ai_timeout(ctx)

        # Reevaluate lease
        reeval = self._reeval.evaluate(ctx)

        if reeval.outcome == ReevalOutcome.PROPOSE_AI:
            result = DecisionResult(
                action_kind="follow",
                confidence=0.5,
                source="ai",
                reason_codes=["ai_available"],
            )
            self._finalize_trace(trace, result, lease_id=None)
            return result

        lease = reeval.lease

        # Run rule chain with current context
        result = self._chain.run(ctx)

        # Enrich trace
        if lease:
            trace.lease_id = lease.id
            trace.heuristic_id = lease.heuristic_id

        if result.strategy_id:
            trace.strategy_id = result.strategy_id
        if result.rule_id:
            trace.rule_id = result.rule_id

        self._finalize_trace(trace, result, lease_id=lease.id if lease else None)
        return result

    def tick(self, *, now_ts: float | None = None) -> None:
        """Periodic maintenance: sweep expired leases."""
        self._reeval.handle_expiry(now_ts=now_ts)

    def get_current_lease(self, domain: str | None = None) -> HeuristicDecisionLeaseDB | None:
        d = domain or "tui_snake"
        return self._lease_repo.get_active(d)

    def send_ai_event(self, event_kind: str, ctx: DecisionContext | None = None) -> None:
        """Signal AI lifecycle events to the state machine."""
        event: dict[str, Any] = {"kind": event_kind, "timestamp": time.time()}
        self._state_machine.send(event)
        if event_kind == "ai_request_sent":
            self._last_ai_request_at = time.time()

    @property
    def state_name(self) -> str:
        return self._state_machine.state_name

    @property
    def is_fallback_active(self) -> bool:
        return self._state_machine.state_name == "fallback_active"

    def get_metrics(self) -> dict[str, Any]:
        return {s: m.to_dict() for s, m in self._metrics.all().items()}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _tick_ai_timeout(self, ctx: DecisionContext) -> None:
        if (
            self._state_machine.state_name == "waiting_ai"
            and self._last_ai_request_at is not None
            and (time.time() - self._last_ai_request_at) > _AI_TIMEOUT_SECONDS
        ):
            self._state_machine.send({"kind": "ai_timeout", "timestamp": time.time()})
        elif ctx.ai_status in ("timeout", "offline"):
            if self._state_machine.state_name not in ("fallback_active", "disabled"):
                self._state_machine.send({"kind": "ai_timeout", "timestamp": time.time()})

    def _finalize_trace(
        self,
        trace: DecisionTrace,
        result: DecisionResult,
        lease_id: str | None,
    ) -> None:
        trace.confidence = result.confidence
        trace.fallback_reason = result.fallback_reason
        trace.source = result.source
        trace.action_kind = result.action_kind
        trace.reason_codes = list(result.reason_codes)
        if lease_id and not trace.lease_id:
            trace.lease_id = lease_id
        trace.resolve()
        self._metrics.record(trace)
        try:
            self._trace_repo.save(trace)
        except Exception:
            pass  # trace persistence must not block decisions
