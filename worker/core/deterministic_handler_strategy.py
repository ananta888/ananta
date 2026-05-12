"""DeterministicHandlerStrategy — FA-T006 wrapper for TaskHandlerRegistry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import (
    ProposeStrategyResult,
    ExecutableProposal,
    ProposalBase,
)

from agent.services.task_handler_registry import get_task_handler_registry


def coerce_handler_response(response: Any) -> Dict[str, Any] | None:
    """Coerce handler.propose response to dict payload."""
    if response is None:
        return None
    if hasattr(response, "data"):
        return dict(response.data or {})
    if isinstance(response, dict):
        return dict(response)
    return None


class DeterministicHandlerStrategy(ProposeStrategy):
    """Strategy wrapper for TaskHandlerRegistry."""

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        registry = get_task_handler_registry()
        task_kind = context.task.get("kind") or "unknown"
        handler = registry.resolve(task_kind)
        if handler is None or not hasattr(handler, "propose"):
            return ProposeStrategyResult.declined(
                "deterministic_handler",
                reason="no_suitable_handler",
                reason_codes=["no_handler"],
            )
        descriptor = registry.resolve_descriptor(task_kind) or {}
        # Mock service/forwarder/request_data as needed; adapt as per handler contract
        response = handler.propose(
            tid=context.task_id,
            task=context.task,
            task_kind=task_kind,
            request_data={},  # Stub
            base_prompt=context.base_prompt,
            service=None,  # Stub, adapt if needed
            cli_runner=context.cli_runner,
            forwarder=None,  # Stub
            tool_definitions_resolver=context.tool_definitions_resolver,
            handler_descriptor=descriptor,
        )
        payload = coerce_handler_response(response)
        if payload is None:
            return ProposeStrategyResult.declined(
                "deterministic_handler",
                "handler_returned_none",
            )
        # Try to normalize to ExecutableProposal
        try:
            proposal = ExecutableProposal(
                proposal_id=payload.get("proposal_id", f"prop-{id(payload)}"),
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="deterministic_handler",
                command=payload.get("command"),
                tool_calls=payload.get("tool_calls", []),
                expected_artifacts=payload.get("expected_artifacts", []),
                safety_flags=dict(descriptor.get("safety_flags", {})),
                metadata=dict(payload),
            )
            return ProposeStrategyResult.executable("deterministic_handler", proposal)
        except ValueError:
            # Invalid executable, treat as advisory
            return ProposeStrategyResult.advisory(
                "deterministic_handler",
                advisory_text=str(payload),
                reason="handler_non_executable_output",
            )