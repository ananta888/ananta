import pytest
from src.controller.agent import ControllerAgent


def create_agent() -> ControllerAgent:
    return ControllerAgent(
        name="controller",
        provider="internal",
        model="none",
        prompt_template="",
        config_path="",
    )


def test_assign_task_skips_blacklisted():
    agent = create_agent()
    agent.tasks = ["a", "b"]
    agent.update_blacklist("a")
    assert agent.assign_task() == "b"
    assert agent.assign_task() is None
    assert agent.log_status() == ["blacklisted:a", "assigned:b"]
    log_copy = agent.log_status()
    log_copy.append("changed")
    assert "changed" not in agent.log_status()
