from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "test_env_cleanup.py"
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
        patch.object(MODULE, "_docker_inspect_value", side_effect=["starting", "healthy", "healthy"]),
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
