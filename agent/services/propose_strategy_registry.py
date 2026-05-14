"""ProposeStrategyRegistry — builds real strategy dict. FA-T021."""
from __future__ import annotations

from typing import Dict

from worker.core.propose_orchestrator import ProposeStrategy
from worker.core.deterministic_handler_strategy import DeterministicHandlerStrategy
from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy
from worker.core.json_schema_llm_strategy import JsonSchemaLLMStrategy
from worker.core.worker_strategy import WorkerStrategy
from worker.core.agent_loop_tool_calling_strategy import AgentLoopToolCallingStrategy
from worker.core.cli_agent_patch_strategy import CliAgentPatchStrategy
from worker.core.hermes_proposal_strategy import HermesProposalStrategy
from agent.services.propose_strategies.flexible_llm_normalization_strategy import (
    FlexibleLLMNormalizationStrategy,
)
from agent.services.propose_strategies.advisory_proposal_strategy import AdvisoryProposalStrategy
from agent.services.propose_strategies.human_review_strategy import HumanReviewStrategy


def build_strategy_registry() -> Dict[str, ProposeStrategy]:
    """Return a dict of strategy_id → real ProposeStrategy for all known strategies.

    Unregistered strategy_ids requested via ProposePolicy are handled by the
    orchestrator as strategy_not_available (declined with diagnostics).
    No StubStrategy is used for any registered id.
    """
    return {
        "agent_loop_tool_calling": AgentLoopToolCallingStrategy(),
        "cli_agent_patch_strategy": CliAgentPatchStrategy(),
        "hermes_proposal_strategy": HermesProposalStrategy(),
        "deterministic_handler": DeterministicHandlerStrategy(),
        "worker_strategy": WorkerStrategy(),
        "tool_calling_llm": ToolCallingLLMStrategy(),
        "json_schema_llm": JsonSchemaLLMStrategy(),
        "flexible_llm_normalization": FlexibleLLMNormalizationStrategy(),
        "advisory_proposal": AdvisoryProposalStrategy(),
        "human_review": HumanReviewStrategy(),
    }
