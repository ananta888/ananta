from __future__ import annotations

from unittest.mock import patch

from agent.models import TaskExecutionPolicyContract
from agent.services.task_execution_service import TaskExecutionService


class _StubShell:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.outputs: dict[str, tuple[str, int]] = {}

    def execute(self, command: str, timeout: int = 30) -> tuple[str, int]:
        self.commands.append(command)
        return self.outputs.get(command, ("", 0))


def _policy() -> TaskExecutionPolicyContract:
    return TaskExecutionPolicyContract(
        timeout_seconds=10,
        retries=0,
        retry_delay_seconds=0,
        source="test",
    )


def test_execute_shell_command_with_workdir_avoids_chain_operator() -> None:
    service = TaskExecutionService()
    shell = _StubShell()
    with patch("agent.services.task_execution_service.get_shell", return_value=shell):
        service._execute_shell_command_with_policy(
            tid="T-1",
            command="pwd",
            execution_policy=_policy(),
            working_directory="/tmp/demo dir",
        )

    assert len(shell.commands) == 1
    assert shell.commands[0] == "cd '/tmp/demo dir'\npwd"
    assert "&&" not in shell.commands[0]


def test_execute_shell_command_without_workdir_keeps_command_unchanged() -> None:
    service = TaskExecutionService()
    shell = _StubShell()
    with patch("agent.services.task_execution_service.get_shell", return_value=shell):
        service._execute_shell_command_with_policy(
            tid="T-2",
            command="python -V",
            execution_policy=_policy(),
            working_directory=None,
        )

    assert len(shell.commands) == 1
    assert shell.commands[0] == "python -V"


def test_execute_shell_command_splits_and_chain_into_multiple_safe_steps() -> None:
    service = TaskExecutionService()
    shell = _StubShell()
    with patch("agent.services.task_execution_service.get_shell", return_value=shell):
        output, exit_code, retries_used, failure_type, retry_history = service._execute_shell_command_with_policy(
            tid="T-3",
            command="mkdir -p app && touch README.md",
            execution_policy=_policy(),
            working_directory="/tmp/work",
        )

    assert exit_code == 0
    assert retries_used == 0
    assert failure_type == "success"
    assert output == ""
    assert len(shell.commands) == 2
    assert shell.commands[0] == "cd /tmp/work\nmkdir -p app"
    assert shell.commands[1] == "cd /tmp/work\ntouch README.md"
    assert all("&&" not in command for command in shell.commands)
    assert any(entry.get("normalization") == "split_and_chain_command" for entry in retry_history)


def test_execute_shell_command_repairs_fragmented_prompt_artifacts() -> None:
    service = TaskExecutionService()
    shell = _StubShell()
    with patch("agent.services.task_execution_service.get_shell", return_value=shell):
        service._execute_shell_command_with_policy(
            tid="T-4",
            command="touch README.m> d",
            execution_policy=_policy(),
            working_directory=None,
        )

    assert len(shell.commands) == 1
    assert shell.commands[0] == "touch README.md"


def test_execute_shell_command_rejects_unsupported_or_chaining() -> None:
    service = TaskExecutionService()
    shell = _StubShell()
    with patch("agent.services.task_execution_service.get_shell", return_value=shell):
        output, exit_code, retries_used, failure_type, retry_history = service._execute_shell_command_with_policy(
            tid="T-5",
            command="echo fail || echo fallback",
            execution_policy=_policy(),
            working_directory=None,
        )

    assert exit_code == -1
    assert retries_used == 0
    assert failure_type == "command_runtime_error"
    assert "Unsupported shell operators" in output
    assert retry_history == []
    assert shell.commands == []
