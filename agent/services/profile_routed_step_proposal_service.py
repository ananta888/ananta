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
        from agent.services.model_invocation_service import ModelInvocationService
        from agent.services.model_profile_resolver import RoutingContext
        from agent.services.tiny_router.snake_shadow import observe_snake_candidate

        normalized_kind = str(task_kind or "classification").strip().lower()
        observe_snake_candidate(prompt, agent_config=agent_config)
        return ModelInvocationService.invoke(
            prompt,
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
