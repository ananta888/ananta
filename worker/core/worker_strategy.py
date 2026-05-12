"""WorkerStrategy — AFR-T006: real worker delegation or safe decline. No dummy output."""
from __future__ import annotations

from typing import Any, List

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult

from agent.services.worker_runtime_selection_service import (
    WorkerRuntimeSelectionService,
    WorkerRuntimeSelectionRequest,
)
from worker.core.runtime_target import (
    WorkerSelectionPolicy,
    WorkerSelectionMode,
    SelectionDecisionStatus,
)


class WorkerStrategy(ProposeStrategy):
    """Delegates propose to a selected worker (OpenCode/Hermes/native).

    When no real worker is available, returns declined with diagnostics.
    Never generates dummy/mock command output.
    """

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        try:
            policy = WorkerSelectionPolicy(
                mode=WorkerSelectionMode.automatic,
                required_capabilities=["llm_reasoning", "propose"],
            )

            # Resolve real candidates. Currently no registered workers → declined.
            workers: List[Any] = []
            runtime_targets: List[Any] = []

            request = WorkerRuntimeSelectionRequest(
                policy=policy,
                workers=workers,
                runtime_targets=runtime_targets,
                required_capabilities=["propose"],
                execution_mode="propose",
                policy_decision_ref=context.task_id,
                context_boundary_decision="local_preferred",
            )

            service = WorkerRuntimeSelectionService()
            decision = service.select(request)

            if decision.status != SelectionDecisionStatus.selected:
                return ProposeStrategyResult.declined(
                    "worker_strategy",
                    reason=str(decision.reason) if hasattr(decision, "reason") else "no_worker_selected",
                    reason_codes=["no_worker_selected"] + list(getattr(decision, "reason_codes", [])),
                )

            # Worker selected: real delegation path (future implementation)
            # For now, decline with explicit diagnostic instead of generating mock output
            return ProposeStrategyResult.declined(
                "worker_strategy",
                reason="real_worker_delegation_not_implemented",
                reason_codes=["worker_delegation_todo"],
            )

        except Exception as exc:
            return ProposeStrategyResult.declined(
                "worker_strategy",
                reason=f"worker_strategy_error: {exc}",
                reason_codes=["worker_error"],
            )
