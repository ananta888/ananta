"""ProposeStrategyOrchestrator — central policy-driven proposal engine. FA-T005."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from ananta_contracts.model_recovery import aggregate_model_recovery_signals
from ananta_contracts.propose_context import ProposeContext  # noqa: F401
from ananta_contracts.propose_policy import LLM_STRATEGY_IDS, ProposePolicy
from worker.core.propose import (
    STATUS_DECLINED,
    ProposeStrategyResult,
)


class ProposeStrategy(ABC):
    """Abstract propose strategy."""

    @abstractmethod
    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        """Run strategy, return result."""
        ...


class ProposeStrategyOrchestrator:
    """Orchestrates strategies per policy.

    Iterates the full effective_strategy_order. Each strategy is tried once.
    The first executable or terminal result stops the chain.

    T002: max_strategy_attempts no longer truncates the strategy chain —
    it remains in ProposePolicy for per-strategy retry control (future use).
    T003: when llm_required=True and all LLM strategies returned
    llm_required_but_unavailable, returns terminal needs_review before
    falling through to deterministic/template strategies.
    """

    def __init__(self, policy: ProposePolicy, strategies: Dict[str, "ProposeStrategy"]):
        self.policy = policy
        self.strategies = strategies

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        """Run full strategy chain. Returns first executable or terminal result."""
        strategy_order = self.policy.effective_strategy_order()
        attempted: List[Dict] = []
        collected_llm_profiles: List[Dict[str, Any]] = []
        collected_fallback_decisions: List[Dict[str, Any]] = []
        collected_recovery_signals: List[Dict[str, Any]] = []

        llm_required = self.policy.llm_required
        llm_unavailable_count = 0
        llm_strategy_count = sum(
            1 for s in strategy_order if s in LLM_STRATEGY_IDS and self.policy.is_strategy_enabled(s)
        )

        for strategy_id in strategy_order:
            if not self.policy.is_strategy_enabled(strategy_id):
                attempted.append(
                    {
                        "strategy_id": strategy_id,
                        "status": STATUS_DECLINED,
                        "reason": "disabled_by_strategy_mode",
                    }
                )
                continue

            strategy = self.strategies.get(strategy_id)
            if strategy is None:
                result = ProposeStrategyResult.declined(strategy_id, reason="strategy_not_available")
            else:
                result = strategy.run(context)
            if isinstance(getattr(result, "metadata", None), dict):
                entries = list((result.metadata or {}).get("llm_call_profile") or [])
                for entry in entries:
                    if isinstance(entry, dict):
                        collected_llm_profiles.append(dict(entry))
                decisions = list((result.metadata or {}).get("fallback_decisions") or [])
                for decision in decisions:
                    if isinstance(decision, dict):
                        collected_fallback_decisions.append(dict(decision))
                signal = (result.metadata or {}).get("model_recovery_signal")
                if isinstance(signal, dict):
                    signal = dict(signal)
                    signal["strategy_failures"] = [
                        {
                            "strategy_id": strategy_id,
                            "terminal_reason": signal.get("terminal_reason"),
                            "attempt_count": signal.get("attempt_count"),
                        }
                    ]
                    collected_recovery_signals.append(signal)

            attempted.append(
                {
                    "strategy_id": strategy_id,
                    "status": result.status,
                    "reason": result.reason,
                }
            )

            # Track LLM unavailability for T003 enforcement
            if strategy_id in LLM_STRATEGY_IDS and result.status == STATUS_DECLINED:
                if result.reason and "llm_required_but_unavailable" in result.reason:
                    llm_unavailable_count += 1

            if result.is_executable or result.is_terminal:
                if isinstance(result.metadata, dict):
                    result.metadata["attempted_strategies"] = attempted
                    result.metadata["selected_strategy"] = strategy_id
                    if collected_llm_profiles:
                        result.metadata["llm_call_profile"] = list(collected_llm_profiles)
                    if collected_fallback_decisions:
                        result.metadata["fallback_decisions"] = list(collected_fallback_decisions[-32:])
                    if not result.is_executable:
                        recovery_signal = aggregate_model_recovery_signals(collected_recovery_signals)
                        if recovery_signal is not None:
                            result.metadata["model_recovery_signal"] = recovery_signal
                return result

            # T003: after last LLM strategy, enforce llm_required
            if (
                llm_required
                and llm_strategy_count > 0
                and llm_unavailable_count == llm_strategy_count
                and strategy_id == _last_llm_strategy([s for s in strategy_order if self.policy.is_strategy_enabled(s)])
            ):
                meta = {
                    "attempted_strategies": attempted,
                    "selected_strategy": None,
                    "llm_required_enforced": True,
                }
                if collected_llm_profiles:
                    meta["llm_call_profile"] = list(collected_llm_profiles)
                if collected_fallback_decisions:
                    meta["fallback_decisions"] = list(collected_fallback_decisions[-32:])
                recovery_signal = aggregate_model_recovery_signals(collected_recovery_signals)
                if recovery_signal is not None:
                    meta["model_recovery_signal"] = recovery_signal
                return ProposeStrategyResult.needs_review(
                    "orchestrator",
                    "llm_required_but_unavailable",
                    reason_codes=["llm_required", "llm_provider_unavailable", "no_llm_fallback_allowed"],
                    metadata=meta,
                )

        # All strategies declined
        fallback_meta = {"attempted_strategies": attempted, "selected_strategy": None}
        if collected_llm_profiles:
            fallback_meta["llm_call_profile"] = list(collected_llm_profiles)
        if collected_fallback_decisions:
            fallback_meta["fallback_decisions"] = list(collected_fallback_decisions[-32:])
        recovery_signal = aggregate_model_recovery_signals(collected_recovery_signals)
        if recovery_signal is not None:
            fallback_meta["model_recovery_signal"] = recovery_signal
        match self.policy.on_all_strategies_declined:
            case "needs_review":
                r = ProposeStrategyResult.needs_review(
                    "orchestrator",
                    "all_strategies_declined_needs_review",
                    metadata=fallback_meta,
                )
            case "failed":
                r = ProposeStrategyResult.failed(
                    "orchestrator",
                    "all_strategies_declined_failed",
                    metadata=fallback_meta,
                )
            case "advisory":
                r = ProposeStrategyResult.advisory(
                    "orchestrator",
                    advisory_text="All strategies declined; human review recommended.",
                    metadata=fallback_meta,
                )
            case _:
                raise ValueError(f"invalid_on_all_strategies_declined: {self.policy.on_all_strategies_declined}")
        return r


def _last_llm_strategy(strategy_order: list[str]) -> str | None:
    """Return the last LLM strategy id in the order, or None."""
    last = None
    for s in strategy_order:
        if s in LLM_STRATEGY_IDS:
            last = s
    return last


class StubStrategy(ProposeStrategy):
    """Placeholder for unimplemented strategies. Always declines with diagnostics."""

    def __init__(self, strategy_id: str):
        self.strategy_id = strategy_id

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        return ProposeStrategyResult.declined(self.strategy_id, "stub_not_implemented_yet")
