from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT_PATH = Path(__file__).resolve().with_name("test_env_cleanup.py")
SPEC = importlib.util.spec_from_file_location("ananta_test_env_cleanup", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_cleanup_ollama_runtime_returns_early_when_no_models_loaded():
    with (
        patch.object(MODULE, "_list_loaded_ollama_models", return_value=[]),
        patch.object(MODULE, "_docker_inspect_value", return_value="healthy"),
        patch.object(MODULE, "_run_command") as run_command,
    ):
        summary = MODULE.cleanup_ollama_runtime(ollama_container="ollama")

    assert summary["before"] == []
    assert summary["after_restart"] == []
    assert summary["restarted"] is False
    assert summary["health"] == "healthy"
    run_command.assert_not_called()


def test_cleanup_ollama_runtime_restarts_ollama_when_stop_does_not_unload_model():
    loaded_states = [
        ["ananta-default:latest"],
        ["ananta-default:latest"],
        [],
    ]
    run_result = Mock(returncode=0, stdout="ok", stderr="")

    with (
        patch.object(MODULE, "_list_loaded_ollama_models", side_effect=lambda *_args, **_kwargs: loaded_states.pop(0)),
        patch.object(MODULE, "_docker_inspect_value", side_effect=["healthy", "healthy"]),
        patch.object(MODULE.time, "monotonic", side_effect=[0.0, 0.5, 2.1, 0.0, 0.5]),
        patch.object(MODULE.time, "sleep", return_value=None),
        patch.object(MODULE, "_run_command", return_value=run_result) as run_command,
    ):
        summary = MODULE.cleanup_ollama_runtime(ollama_container="ollama", stop_timeout_seconds=1, health_timeout_seconds=1)

    assert [item["model"] for item in summary["stopped"]] == ["ananta-default:latest"]
    assert summary["after_stop"] == ["ananta-default:latest"]
    assert summary["restarted"] is True
    assert summary["after_restart"] == []
    assert summary["health"] == "healthy"
    assert run_command.call_args_list[0].args[0] == ["docker", "exec", "ollama", "ollama", "stop", "ananta-default:latest"]
    assert run_command.call_args_list[1].args[0] == ["docker", "restart", "ollama"]


def test_cleanup_test_environment_restarts_agents_when_goal_cleanup_occurs():
    with (
        patch.object(MODULE, "login_token", return_value="token"),
        patch.object(MODULE, "req", return_value={"status": "success"}),
        patch.object(MODULE, "_list_resources", return_value=[]),
        patch.object(MODULE, "_cleanup_goal_data_in_agent", return_value={"goal_ids": ["goal-1"], "task_ids": ["task-1"]}),
        patch.object(MODULE, "_restart_agent_containers", return_value={"restarted": ["hub", "alpha", "beta"]}) as restart_agents,
    ):
        summary = MODULE.cleanup_test_environment(
            hub_base_url="http://hub",
            hub_container="hub",
            agent_containers=["alpha", "beta"],
            admin_user="admin",
            admin_password="secret",
            explicit_targets={"goal_ids": ["goal-1"]},
        )

    assert summary["goal_cleanup"]["goal_ids"] == ["goal-1"]
    assert summary["goal_cleanup"]["task_ids"] == ["task-1"]
    restart_agents.assert_called_once_with(["hub", "alpha", "beta"])
    assert summary["agent_restart"] == {"restarted": ["hub", "alpha", "beta"]}
