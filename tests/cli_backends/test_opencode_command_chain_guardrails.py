"""SCG-009: OpenCode command normalization tests — both command-string and tool-call paths."""
from __future__ import annotations

from unittest.mock import patch

from agent.models import TaskExecutionPolicyContract
from agent.services.execution_risk_policy_service import ExecutionRiskDecision
from agent.services.task_execution_service import TaskExecutionService

_OPENCODE_CMD = 'python hello.py; python -c "from hello import greet; print(greet(\'World\'))"'


class _StubShell:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.outputs: dict[str, tuple[str, int]] = {}

    def execute(self, command: str, timeout: int = 30) -> tuple[str, int]:
        self.commands.append(command)
        return self.outputs.get(command, ("ok", 0))


def _policy() -> TaskExecutionPolicyContract:
    return TaskExecutionPolicyContract(timeout_seconds=10, retries=0, retry_delay_seconds=0, source="test")


def _allow_risk(*, command, tool_calls, task, agent_cfg, command_analysis=None):
    return ExecutionRiskDecision(True, False, "low", [], [], {})


def _deny_rm_risk(*, command, tool_calls, task, agent_cfg, command_analysis=None):
    if "rm -rf" in str(command or ""):
        return ExecutionRiskDecision(False, False, "critical", ["execution_risk_denied:critical"], [], {})
    return ExecutionRiskDecision(True, False, "low", [], [], {})


_TASK = {"worker_execution_context": {}}
_CFG = {"execution_risk_policy": {"enabled": True, "task_scoped_only": False}}


def test_opencode_command_string_python_semicolon_chain_allowed():
    """Command string path: OpenCode python semicolon chain must not be blocked."""
    svc = TaskExecutionService()
    shell = _StubShell()
    shell.outputs = {
        "python hello.py": ("hello", 0),
        'python -c "from hello import greet; print(greet(\'World\'))"': ("World", 0),
    }
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        _, code, _, _, history = svc._execute_shell_command_with_policy(
            tid="OC-1",
            command=_OPENCODE_CMD,
            execution_policy=_policy(),
            task=_TASK,
            agent_cfg=_CFG,
        )
    assert code == 0
    assert len(shell.commands) == 2
    assert shell.commands[0] == "python hello.py"
    assert "python -c" in shell.commands[1]
    chain_entry = next((h for h in history if h.get("normalization") == "command_chain_parsed"), None)
    assert chain_entry is not None
    assert chain_entry["segment_count"] == 2


def test_opencode_python_c_quoted_semicolon_not_split():
    """The Python-level semicolons inside the -c argument must not be treated as shell splits."""
    svc = TaskExecutionService()
    shell = _StubShell()
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        svc._execute_shell_command_with_policy(
            tid="OC-2",
            command=_OPENCODE_CMD,
            execution_policy=_policy(),
            task=_TASK,
            agent_cfg=_CFG,
        )
    # Exactly 2 commands: no further splitting of the quoted python -c argument
    assert len(shell.commands) == 2
    second_cmd = shell.commands[1]
    assert second_cmd.count(";") >= 1  # the python-level semicolons are intact


def test_opencode_command_string_pipe_blocked():
    """OpenCode command with pipe must be blocked before any execution."""
    svc = TaskExecutionService()
    shell = _StubShell()
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        _, code, _, _, _ = svc._execute_shell_command_with_policy(
            tid="OC-3",
            command="cat hello.py | grep def",
            execution_policy=_policy(),
            task=_TASK,
            agent_cfg=_CFG,
        )
    assert code == -1
    assert shell.commands == []


def test_opencode_tool_call_rm_segment_blocked_before_any_execution():
    """Even with a safe first segment, a dangerous second segment must block the whole chain."""
    svc = TaskExecutionService()
    shell = _StubShell()
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_deny_rm_risk),
    ):
        _, code, _, _, history = svc._execute_shell_command_with_policy(
            tid="OC-4",
            command="python hello.py; rm -rf /tmp/x",
            execution_policy=_policy(),
            task=_TASK,
            agent_cfg=_CFG,
        )
    assert code == -1
    assert shell.commands == []
    chain_info = next((h.get("command_chain") for h in history if h.get("command_chain")), None)
    assert chain_info is not None
    validations = chain_info.get("validations") or []
    denied = [v for v in validations if not v.get("allowed")]
    assert denied
    assert denied[0]["segment_index"] == 2


def test_opencode_shell_execute_tool_call_with_pipe_blocked():
    """shell_execute tool call with pipe in command arg must be blocked (SCG-008)."""
    from agent.exceptions import ToolGuardrailError
    svc = TaskExecutionService()
    shell = _StubShell()
    tool_calls = [{"name": "shell_execute", "args": {"command": "cat hello.py | grep def"}}]
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        try:
            svc.execute_local_step(
                tid="OC-5",
                task=_TASK,
                command=None,
                tool_calls=tool_calls,
                execution_policy=_policy(),
                guard_cfg=_CFG,
            )
            assert False, "Expected ToolGuardrailError"
        except ToolGuardrailError as e:
            assert any("shell_operator_unsupported" in str(r) for r in e.details.get("blocked_reasons", []))
    assert shell.commands == []
