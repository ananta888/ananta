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
    return TaskExecutionPolicyContract(timeout_seconds=10, retries=0, retry_delay_seconds=0, source="e2e")


def _allow_risk(*, command, tool_calls, task, agent_cfg, command_analysis=None):
    return ExecutionRiskDecision(True, False, "low", [], [], {})


def _deny_git_diff(*, command, tool_calls, task, agent_cfg, command_analysis=None):
    if str(command or "").strip() == "python -c 'print(2)'":
        return ExecutionRiskDecision(False, False, "high", ["execution_risk_denied:high"], [], {})
    return ExecutionRiskDecision(True, False, "low", [], [], {})


def test_chain_semantics_and_prevalidation() -> None:
    svc = TaskExecutionService()
    shell = _StubShell()
    shell.outputs = {
        "python -c 'print(1)'": ("1", 0),
        "python -c 'raise SystemExit(1)'": ("", 1),
        "python -c 'print(3)'": ("3", 0),
    }
    with (
        patch("agent.services.task_execution_service.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
        patch("agent.services.segment_preflight_validator.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        out, code, _, _, history = svc._execute_shell_command_with_policy(
            tid="E2E-CC-1",
            command="python -c 'print(1)' && python -c 'raise SystemExit(1)' || python -c 'print(3)'",
            execution_policy=_policy(),
            task={"worker_execution_context": {}},
            agent_cfg={"execution_risk_policy": {"enabled": True, "task_scoped_only": False}},
        )
    assert code == 0
    assert "3" in out
    assert len(shell.commands) == 3
    assert any(item.get("normalization") == "command_chain_parsed" and item.get("segment_count") == 3 for item in history)


def test_denied_later_segment_prevents_earlier_execution() -> None:
    svc = TaskExecutionService()
    shell = _StubShell()
    with (
        patch("agent.services.task_execution_service.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_deny_git_diff),
    ):
        _, code, _, _, _ = svc._execute_shell_command_with_policy(
            tid="E2E-CC-2",
            command="python -c 'print(1)'; python -c 'print(2)'",
            execution_policy=_policy(),
            task={"worker_execution_context": {}},
            agent_cfg={"execution_risk_policy": {"enabled": True, "task_scoped_only": False}},
        )
    assert code == -1
    assert shell.commands == []


# SCG-018: OpenCode mini-project E2E flow


def _deny_rm(*, command, tool_calls, task, agent_cfg, command_analysis=None):
    if "rm -rf" in str(command or ""):
        return ExecutionRiskDecision(False, False, "critical", ["execution_risk_denied:critical"], [], {})
    return ExecutionRiskDecision(True, False, "low", [], [], {})


def test_opencode_mini_project_semicolon_chain_completes() -> None:
    """Simulate an OpenCode proposal that chains a run and test command."""
    svc = TaskExecutionService()
    shell = _StubShell()
    opencode_cmd = 'python hello.py; python -c "from hello import greet; print(greet(\'World\'))"'
    shell.outputs = {
        "python hello.py": ("hello", 0),
        'python -c "from hello import greet; print(greet(\'World\'))"': ("World", 0),
    }
    with (
        patch("agent.services.task_execution_service.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        out, code, _, _, history = svc._execute_shell_command_with_policy(
            tid="E2E-OC-1",
            command=opencode_cmd,
            execution_policy=_policy(),
            task={"worker_execution_context": {}},
            agent_cfg={"execution_risk_policy": {"enabled": True, "task_scoped_only": False}},
        )
    assert code == 0
    assert len(shell.commands) == 2
    chain_entry = next((h for h in history if h.get("normalization") == "command_chain_parsed"), None)
    assert chain_entry is not None
    assert chain_entry["segment_count"] == 2
    guardrail_blocked = any(h.get("event_type") == "tool_guardrail_blocked" for h in history)
    assert not guardrail_blocked


def test_opencode_dangerous_variant_blocked_before_any_execution() -> None:
    """Same project but with a dangerous second segment: no execution must happen."""
    svc = TaskExecutionService()
    shell = _StubShell()
    dangerous_cmd = "python hello.py; rm -rf /tmp/hello_output"
    with (
        patch("agent.services.task_execution_service.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_deny_rm),
    ):
        _, code, _, _, _ = svc._execute_shell_command_with_policy(
            tid="E2E-OC-2",
            command=dangerous_cmd,
            execution_policy=_policy(),
            task={"worker_execution_context": {}},
            agent_cfg={"execution_risk_policy": {"enabled": True, "task_scoped_only": False}},
        )
    assert code == -1
    assert shell.commands == []
