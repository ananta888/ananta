"""ChatDecisionManager — decision coordinator for Chat/CodeCompass surface.

Wires together:
  ChatQueryClassifier   → intent classification (no LLM)
  Chat selector chain   → context selection
  ChatStateMachine      → lifecycle (waiting_ai / heuristic_context_selection / stale)
  LeaseReevaluationService → TTL management
  DecisionTrace         → per-decision audit record

Late AI response handling:
  If AI returns after the heuristic has already answered (context_hash changed
  or state != waiting_ai), the response is discarded via handle_late_ai_response().

chat_policy integration:
  ChatDecisionManager does NOT replace chat_policy — it delegates policy checks
  to whatever ChatAccessPolicy is wired in at construction time.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from agent.db_models import HeuristicDecisionLeaseDB
from agent.repositories.decision_trace_repo import DecisionTraceRepository
from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository
from agent.services.heuristic_runtime.chat_query_classifier import ChatQueryClassifier, ClassificationResult, IntentKind
from agent.services.heuristic_runtime.chat_selectors import build_chat_selector_chain
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.decision_trace import DecisionTrace, DecisionMetricsAccumulator
from agent.services.heuristic_runtime.heuristic_registry_service import get_heuristic_registry
from agent.services.heuristic_runtime.lease_reevaluation_service import LeaseReevaluationService, ReevalOutcome
from agent.services.heuristic_runtime.state_machine import ChatStateMachine


# PolicyCheck: callable(query, ctx) -> (allowed: bool, reason: str)
PolicyCheck = Callable[[str, DecisionContext], tuple[bool, str]]


def _default_policy(query: str, ctx: DecisionContext) -> tuple[bool, str]:
    return True, ""


class ChatDecisionManager:
    def __init__(
        self,
        lease_repo: HeuristicLeaseRepository | None = None,
        trace_repo: DecisionTraceRepository | None = None,
        policy_check: PolicyCheck | None = None,
    ) -> None:
        self._lease_repo = lease_repo or HeuristicLeaseRepository()
        self._trace_repo = trace_repo or DecisionTraceRepository()
        self._policy_check = policy_check or _default_policy
        self._registry = get_heuristic_registry()
        self._reeval = LeaseReevaluationService(repo=self._lease_repo, registry=self._registry)
        self._classifier = ChatQueryClassifier()
        self._selector_chain = build_chat_selector_chain()
        self._state_machine = ChatStateMachine()
        self._metrics = DecisionMetricsAccumulator()
        self._last_context_hash: str = ""

    # ── Public API ────────────────────────────────────────────────────────────

    def decide(self, query: str, ctx: DecisionContext) -> DecisionResult:
        """Classify intent and select context. Returns DecisionResult."""
        trace = DecisionTrace(
            surface=ctx.source_surface,
            context_hash=ctx.context_hash,
            started_at=time.time(),
        )

        # Policy gate (delegates to chat_policy / ChatAccessPolicy)
        allowed, policy_reason = self._policy_check(query, ctx)
        if not allowed:
            result = DecisionResult.policy_denied(policy_reason)
            self._state_machine.send({"kind": "policy_denied"})
            self._finalize_trace(trace, result, lease_id=None)
            return result

        # Classify intent
        classification = self._classifier.classify(query, ctx)

        # Inject query signal into context for selector chain
        ctx_with_query = self._inject_query_event(ctx, query)

        # Reevaluate lease
        reeval = self._reeval.evaluate(ctx_with_query)

        if reeval.outcome == ReevalOutcome.PROPOSE_AI:
            result = DecisionResult(
                action_kind="chat",
                confidence=0.5,
                source="ai",
                answer_kind="ai_answer" if False else None,
                reason_codes=["ai_available", f"intent:{classification.intent_kind.value}"],
            )
            self._state_machine.send({"kind": "ai_request_sent"})
            self._last_context_hash = ctx.context_hash
            self._finalize_trace(trace, result, lease_id=None)
            return result

        # Use heuristic selector chain
        result = self._selector_chain.run(ctx_with_query)

        # Enrich with classification
        result.reason_codes = list(result.reason_codes) + [f"intent:{classification.intent_kind.value}"]

        lease = reeval.lease

        # Drive state machine: waiting_ai → heuristic_context_selection → heuristic_answer_ready
        if self._state_machine.state_name == "waiting_ai":
            ai_ev = "ai_timeout" if ctx.ai_status == "timeout" else "ai_offline"
            self._state_machine.send({"kind": ai_ev, "timestamp": time.time()})
        if self._state_machine.state_name == "heuristic_context_selection":
            event_kind = "no_match" if result.is_no_good_match() else "heuristic_answer_ready"
            self._state_machine.send({"kind": event_kind})
        self._last_context_hash = ctx.context_hash
        self._finalize_trace(trace, result, lease_id=lease.id if lease else None)
        return result

    def handle_late_ai_response(
        self,
        ai_result: DecisionResult,
        original_context_hash: str,
    ) -> bool:
        """Handle a late AI response. Returns True if accepted, False if discarded."""
        # Discard if context has changed or we're no longer in waiting_ai
        if self._last_context_hash != original_context_hash:
            self._state_machine.send({"kind": "ai_response_received"})
            return False

        if self._state_machine.state_name not in ("waiting_ai",):
            self._state_machine.send({"kind": "ai_response_received"})
            return False

        self._state_machine.send({"kind": "ai_response_received"})
        return True

    @property
    def state_name(self) -> str:
        return self._state_machine.state_name

    def get_metrics(self) -> dict[str, Any]:
        return {s: m.to_dict() for s, m in self._metrics.all().items()}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _inject_query_event(self, ctx: DecisionContext, query: str) -> DecisionContext:
        """Return a copy of ctx with the query as a recent normalized event."""
        from dataclasses import replace
        events = list(ctx.recent_events) + [{
            "event_id": "query",
            "kind": "chat_message",
            "normalized_value": query[:200],
            "ref_id": None,
            "timestamp": time.time(),
        }]
        return DecisionContext(
            source_surface=ctx.source_surface,
            ai_status=ctx.ai_status,
            active_goal_id=ctx.active_goal_id,
            active_task_id=ctx.active_task_id,
            selected_artifacts=list(ctx.selected_artifacts),
            active_panel=ctx.active_panel,
            recent_events=events[-20:],
            allowed_source_scopes=list(ctx.allowed_source_scopes),
            policy_state=ctx.policy_state,
        )

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
        trace.lease_id = lease_id
        trace.resolve()
        self._metrics.record(trace)
        try:
            self._trace_repo.save(trace)
        except Exception:
            pass
