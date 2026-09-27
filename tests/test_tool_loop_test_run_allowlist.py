"""WCRB-001: the worker tool loop hands test.run the allowlist configured in the mutation section."""

import pytest

from agent.cli_backends import tool_loop
from agent.services.tools.test_tools import test_run as run_test_command

pytestmark = pytest.mark.timeout(30)


def config(mutation):
    return {"ananta_worker_tool_loop": {"enabled": True, "allowed_tools": ["test.run"]},
            "ananta_worker_workspace_mutation": mutation}


def test_the_mutation_section_allowlist_reaches_the_tool_loop(monkeypatch):
    monkeypatch.setattr(tool_loop, "_get_agent_config",
                        lambda: config({"allowlisted_test_commands": ["python -c pass"], "test_timeout_seconds": 30}))
    cfg = tool_loop.get_tool_loop_config()
    assert cfg["allowlisted_test_commands"] == ["python -c pass"] and cfg["test_timeout_seconds"] == 30
    assert "test_output_max_chars" not in cfg  # only what is configured


def test_test_run_runs_an_allowlisted_command_and_blocks_others(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_loop, "_get_agent_config",
                        lambda: config({"allowlisted_test_commands": ["python -c pass"]}))
    cfg = tool_loop.get_tool_loop_config()
    def run(command):
        return run_test_command(workspace_dir=str(tmp_path), arguments={"command": command}, tool_call_id="t",
                                config=cfg)["status"]

    assert run("python -c pass") == "ok"
    assert run("rm -rf /") == "policy_blocked"


def test_without_a_mutation_section_nothing_is_allowlisted(monkeypatch):
    monkeypatch.setattr(tool_loop, "_get_agent_config", lambda: {"ananta_worker_tool_loop": {}})
    assert "allowlisted_test_commands" not in tool_loop.get_tool_loop_config()
