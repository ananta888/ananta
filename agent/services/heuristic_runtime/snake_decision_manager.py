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

try:
    from agent.services.heuristic_runtime.dsl.loader import DslLoader, DslLoadError
    from agent.services.heuristic_runtime.dsl.validator import DslValidator
    from agent.services.heuristic_runtime.dsl.evaluator import DslEvaluator
    from agent.services.heuristic_runtime.motion_planner import MotionPlanner
    _DSL_RUNTIME_AVAILABLE = True
except ImportError:
    _DSL_RUNTIME_AVAILABLE = False

_AI_TIMEOUT_SECONDS = 2.5
_LURK_IDLE_SECONDS = 3.0        # cursor stable for this long → enter lurk
_LURK_UPDATE_INTERVAL_MS = 500  # max 1 position update per this interval in lurk mode
_LURK_THRESHOLD_PX = 5          # cursor delta below this is considered "stable"


# ── Lurk state manager (T04.04) ───────────────────────────────────────────────

@dataclass
class LurkStateManager:
    """Tracks idle time and rate-limits lurk updates.

    idle_since: timestamp when cursor last had a significant move.
    last_lurk_update: timestamp of the last lurk position update emitted.
    """
    idle_since: float | None = None
    last_lurk_update: float = 0.0
    lurk_update_interval_ms: float = _LURK_UPDATE_INTERVAL_MS
    lurk_idle_seconds: float = _LURK_IDLE_SECONDS
    lurk_threshold_px: int = _LURK_THRESHOLD_PX

    def record_motion(self, dx: int, dy: int, *, now: float | None = None) -> None:
        now = now or time.time()
        magnitude = abs(dx) + abs(dy)
        if magnitude >= self.lurk_threshold_px:
            self.idle_since = None  # reset idle on significant motion
        elif self.idle_since is None:
            self.idle_since = now

    def should_lurk(self, *, now: float | None = None) -> bool:
        """True if cursor has been stable for lurk_idle_seconds."""
        if self.idle_since is None:
            return False
        return (now or time.time()) - self.idle_since >= self.lurk_idle_seconds

    def can_emit_lurk_update(self, *, now: float | None = None) -> bool:
        """Rate-limit: at most 1 lurk position update per lurk_update_interval_ms."""
        now = now or time.time()
        interval_s = self.lurk_update_interval_ms / 1000.0
        if (now - self.last_lurk_update) >= interval_s:
            self.last_lurk_update = now
            return True
        return False

    @property
    def tui_status_indicator(self) -> str:
        """Returns '[L]' when in lurk mode, '[F]' when in fallback follow, '' otherwise."""
        if self.idle_since is not None:
            return "[L]"
        return ""


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
        self._lurk = LurkStateManager()
        self._dsl_loader = DslLoader() if _DSL_RUNTIME_AVAILABLE else None
        self._dsl_validator = DslValidator() if _DSL_RUNTIME_AVAILABLE else None
        self._dsl_evaluator = DslEvaluator() if _DSL_RUNTIME_AVAILABLE else None
        self._motion_planner = MotionPlanner() if _DSL_RUNTIME_AVAILABLE else None

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

        # DSL v2 Runtime: prüfe ob aktive Heuristik dsl_v2 mode hat
        if _DSL_RUNTIME_AVAILABLE and lease:
            dsl_result = self._try_dsl_decide(ctx, lease)
            if dsl_result is not None:
                self._finalize_trace(trace, dsl_result, lease_id=lease.id if lease else None)
                return dsl_result

        # Run rule chain with current context
        result = self._chain.run(ctx)

        # Lurk rate-limiting: if result is lurk, check interval throttle
        if result.action_kind == "lurk" and not self._lurk.can_emit_lurk_update():
            result = DecisionResult.heuristic_lurk(strategy_id=result.strategy_id)

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

    def record_motion(self, dx: int, dy: int) -> None:
        """Feed cursor/snake motion so lurk-idle tracking stays accurate."""
        self._lurk.record_motion(dx, dy)

    @property
    def tui_status_indicator(self) -> str:
        """Status badge for TUI: '[L]' lurk, '[F]' fallback, ''."""
        if self._state_machine.state_name == "fallback_active":
            return "[F]"
        return self._lurk.tui_status_indicator

    @property
    def lurk_state(self) -> LurkStateManager:
        return self._lurk

    def get_metrics(self) -> dict[str, Any]:
        return {s: m.to_dict() for s, m in self._metrics.all().items()}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _try_dsl_decide(self, ctx: DecisionContext, lease: Any) -> DecisionResult | None:
        """Versucht DSL v2 Entscheidung. Gibt None zurück wenn nicht applicable → RuleChain fallback."""
        if not _DSL_RUNTIME_AVAILABLE:
            return None
        try:
            # Hole Heuristik-Definition aus Registry
            heuristic = self._registry.get_by_id(getattr(lease, "heuristic_id", None) or "")
            if heuristic is None:
                return None
            # Lade DSL wenn mode=dsl_v2
            raw_def = getattr(heuristic, "_raw_def", None) or {}
            runtime_mode = (raw_def.get("runtime") or {}).get("mode")
            if runtime_mode != "dsl_v2":
                return None
            dsl = self._dsl_loader.load_from_definition(raw_def)
            val_result = self._dsl_validator.validate(dsl)
            if not val_result.passed:
                return None
            eval_result = self._dsl_evaluator.evaluate(dsl, ctx)
            if not eval_result.matched:
                return None
            decision = self._dsl_evaluator.to_decision_result(eval_result, strategy_id=getattr(heuristic, "heuristic_id", None))
            # Motion Planner wenn target_cell/bbox vorhanden
            target_cell = eval_result.action.get("target_cell")
            target_bbox = eval_result.action.get("target_bbox")
            if self._motion_planner and (target_cell or target_bbox):
                snake_head = (
                    int(getattr(ctx, "snake_head_x", 0) or 0),
                    int(getattr(ctx, "snake_head_y", 0) or 0),
                )
                plan = self._motion_planner.plan(eval_result.action, snake_head)
                from agent.services.heuristic_runtime.decision_result import SuggestedMotion
                decision.suggested_motion = SuggestedMotion(dx=plan.dx, dy=plan.dy)
            return decision
        except Exception:
            return None  # DSL-Fehler dürfen Fast Path nie blockieren

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
