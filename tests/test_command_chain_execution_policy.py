from __future__ import annotations

from unittest.mock import patch

from agent.models import TaskExecutionPolicyContract
from agent.services.execution_risk_policy_service import ExecutionRiskDecision
from agent.services.task_execution_service import TaskExecutionService


class _StubShell:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.outputs: dict[str, tuple[str, int]] = {}

    def execute(self, command: str, timeout: int = 30) -> tuple[str, int]:
        self.commands.append(command)
        return self.outputs.get(command, ("", 0))


def _policy() -> TaskExecutionPolicyContract:
    return TaskExecutionPolicyContract(timeout_seconds=10, retries=0, retry_delay_seconds=0, source="test")


def _allow_risk(*, command, tool_calls, task, agent_cfg):
    return ExecutionRiskDecision(True, False, "low", [], [], {})


def _deny_rm_risk(*, command, tool_calls, task, agent_cfg):
    if "rm -rf" in str(command or ""):
        return ExecutionRiskDecision(False, False, "critical", ["execution_risk_denied:critical"], [], {})
    return ExecutionRiskDecision(True, False, "low", [], [], {})


def test_denied_later_segment_blocks_whole_chain_before_execution() -> None:
    service = TaskExecutionService()
    shell = _StubShell()
    with (
        patch("agent.services.task_execution_service.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_deny_rm_risk),
    ):
        output, exit_code, _, _, _ = service._execute_shell_command_with_policy(
            tid="T-CC2-1",
            command="pytest && rm -rf /tmp/x",
            execution_policy=_policy(),
            task={"worker_execution_context": {}},
            agent_cfg={"execution_risk_policy": {"enabled": True, "task_scoped_only": False}},
        )
    assert exit_code == -1
    assert "segment denied at index 2" in output
    assert shell.commands == []


def test_or_chain_executes_fallback_only_after_failure() -> None:
    service = TaskExecutionService()
    shell = _StubShell()
    shell.outputs = {"pytest": ("failed", 1), "echo failed": ("fallback", 0)}
    with (
        patch("agent.services.task_execution_service.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        output, exit_code, _, _, _ = service._execute_shell_command_with_policy(
            tid="T-CC2-2",
            command="pytest || echo failed",
            execution_policy=_policy(),
            task={"worker_execution_context": {}},
            agent_cfg={"execution_risk_policy": {"enabled": True, "task_scoped_only": False}},
        )
    assert exit_code == 0
    assert "fallback" in output
    assert shell.commands == ["pytest", "echo failed"]


def test_and_chain_skips_next_after_failure() -> None:
    service = TaskExecutionService()
    shell = _StubShell()
    shell.outputs = {"pytest": ("failed", 1)}
    with (
        patch("agent.services.task_execution_service.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        _, exit_code, _, _, history = service._execute_shell_command_with_policy(
            tid="T-CC2-3",
            command="pytest && git status",
            execution_policy=_policy(),
            task={"worker_execution_context": {}},
            agent_cfg={"execution_risk_policy": {"enabled": True, "task_scoped_only": False}},
        )
    assert exit_code == 1
    assert shell.commands == ["pytest"]
    assert any(item.get("skipped_by") == "&&" for item in history)

