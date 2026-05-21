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


def test_learning_phase_observed_json_in_markdown_fence_is_respected():
    svc = PlanningPromptOptimizerService()
    prompt, style = svc.optimize(
        prompt="Return planning tasks as JSON.",
        behavior_profile={
            "preferred_prompt_style": "example_driven_json",
            "learning_state": {"state": "candidate", "observed_output_format": "json_in_markdown_fence"},
        },
    )
    assert "learning phase" in prompt.lower()
    assert "markdown fences" in prompt.lower()


def test_learning_phase_observed_shape_from_learning_state_is_respected():
    svc = PlanningPromptOptimizerService()
    prompt, style = svc.optimize(
        prompt="Return planning tasks as JSON.",
        behavior_profile={
            "preferred_prompt_style": "stepwise_then_json",
            "learning_state": {"state": "candidate", "observed_output_shape": "markdown_bullets"},
        },
    )
    assert "markdown lists are acceptable" in prompt.lower()
    assert style == "stepwise_then_json"
