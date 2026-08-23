"""Worker-side execution port for a Hub-classified profile-routed proposal."""
from __future__ import annotations

from typing import Any, Mapping


class ProfileRoutedStepProposalService:
    """Executes inference only; task-kind selection remains Hub-owned."""

    def propose(
        self,
        prompt: str,
        *,
        task_kind: str,
        agent_config: Mapping[str, Any],
    ) -> str:
        return str(self.propose_detailed(
            prompt, task_kind=task_kind, agent_config=agent_config
        ).get("content") or "")

    def propose_detailed(
        self,
        prompt: str,
        *,
        task_kind: str,
        agent_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute text inference and retain the resolved runtime identity."""
        return self.propose_with_tools(
            prompt, [], task_kind=task_kind, agent_config=agent_config
        )

    def propose_with_tools(
        self,
        prompt: str,
        tools: list[dict],
        *,
        task_kind: str,
        agent_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute a Hub-authorized tool-selection inference on this worker."""
        from agent.services.model_invocation_service import ModelInvocationService
        from agent.services.model_profile_resolver import RoutingContext
        from agent.services.tiny_router.snake_shadow import observe_snake_candidate

        normalized_kind = str(task_kind or "classification").strip().lower()
        observe_snake_candidate(prompt, agent_config=agent_config)
        return ModelInvocationService.invoke_with_tools(
            prompt,
            tools,
            routing_ctx=RoutingContext(
                task_kind=normalized_kind,
                model_role=(
                    "coder"
                    if normalized_kind in {"coding", "debugging", "repo_analysis"}
                    else "any"
                ),
                context_text=prompt,
                allow_cloud=False,
            ),
        )


_SERVICE = ProfileRoutedStepProposalService()


def get_profile_routed_step_proposal_service() -> ProfileRoutedStepProposalService:
    return _SERVICE
