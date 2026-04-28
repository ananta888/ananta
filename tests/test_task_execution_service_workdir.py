from __future__ import annotations

from unittest.mock import patch

from agent.models import TaskExecutionPolicyContract
from agent.services.task_execution_service import TaskExecutionService


class _StubShell:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command: str, timeout: int = 30) -> tuple[str, int]:
        self.commands.append(command)
        return "", 0


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
