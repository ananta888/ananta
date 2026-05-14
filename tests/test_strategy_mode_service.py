from agent.services.strategy_mode_service import StrategyModeService
from agent.services.propose_policy_service import ProposePolicyService


def test_strategy_mode_service_lists_presets():
    svc = StrategyModeService()
    modes = svc.list_modes()
    assert "opencode_like" in modes
    assert "openai_compatible_tool_calling" in modes


def test_strategy_mode_overrides_policy_order():
    psvc = ProposePolicyService()
    policy = psvc.get_effective_policy(
        task_kind="coding",
        project_config={"strategy_mode": "openai_compatible_tool_calling"},
    )
    order = policy.effective_strategy_order()
    assert order[0] == "tool_calling_llm"
    assert "json_schema_llm" in order


def test_strategy_mode_hermes_disables_mutating_fallbacks():
    psvc = ProposePolicyService()
    policy = psvc.get_effective_policy(
        task_kind="new_software_project",
        project_config={"strategy_mode": "hermes_like"},
    )
    assert policy.allow_deterministic_fallback is False
    assert policy.allow_worker_fallback is False
    assert policy.allow_human_review is True
