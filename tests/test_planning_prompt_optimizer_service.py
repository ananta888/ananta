from agent.services.planning_prompt_optimizer_service import PlanningPromptOptimizerService


def test_mermaid_prone_profile_forbids_mermaid_in_prompt():
    svc = PlanningPromptOptimizerService()
    prompt, style = svc.optimize(
        prompt="Return planning tasks as JSON.",
        behavior_profile={"preferred_prompt_style": "mermaid_forbidden", "mermaid_handling": "forbid_in_prompt"},
    )
    assert "Do not output Mermaid" in prompt


def test_markdown_prone_profile_uses_no_fence_contract():
    svc = PlanningPromptOptimizerService()
    prompt, style = svc.optimize(
        prompt="Return planning tasks as JSON.",
        behavior_profile={"preferred_prompt_style": "no_markdown_explicit", "markdown_handling": "forbid"},
    )
    assert "Do not use markdown fences" in prompt
