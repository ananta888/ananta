"""Task-specific proposal routing remains an explicit Hub configuration choice."""

from types import SimpleNamespace

import pytest

from agent.routes.tasks.autopilot_proposal_strategies import _base_propose_payload


@pytest.mark.parametrize("configured", [None, "", "autopilot_no_human_review"])
def test_default_autopilot_proposal_mode_is_preserved(configured):
    loop = SimpleNamespace(_agent_config=lambda: {"autopilot": {"strategy_mode_override": configured}})
    payload = _base_propose_payload("task", loop, lambda _: 32768)
    assert payload == {
        "task_id": "task",
        "context_window": 32768,
        "strategy_mode": "autopilot_no_human_review",
    }


def test_task_default_leaves_backend_selection_to_the_task_proposal_path():
    loop = SimpleNamespace(_agent_config=lambda: {"autopilot": {"strategy_mode_override": "task_default"}})
    assert _base_propose_payload("task", loop, lambda _: 32768) == {
        "task_id": "task",
        "context_window": 32768,
    }


def test_an_explicit_proposal_mode_is_forwarded():
    loop = SimpleNamespace(_agent_config=lambda: {"autopilot": {"strategy_mode_override": "llm_first"}})
    assert _base_propose_payload("task", loop, lambda _: 32768)["strategy_mode"] == "llm_first"
