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


def _allow_risk(*, command, tool_calls, task, agent_cfg, command_analysis=None):
    return ExecutionRiskDecision(True, False, "low", [], [], {})


def _deny_rm_risk(*, command, tool_calls, task, agent_cfg, command_analysis=None):
    if "rm -rf" in str(command or ""):
        return ExecutionRiskDecision(False, False, "critical", ["execution_risk_denied:critical"], [], {})
    return ExecutionRiskDecision(True, False, "low", [], [], {})


def test_denied_later_segment_blocks_whole_chain_before_execution() -> None:
    service = TaskExecutionService()
    shell = _StubShell()
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
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
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
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
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
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


# SCG-014: execute_local_step regression — OpenCode command must pass through preflight


def test_execute_local_step_opencode_chain_not_blocked_by_preflight():
    """execute_local_step must not block the OpenCode python chain via full-string preflight."""
    svc = TaskExecutionService()
    shell = _StubShell()
    cmd = 'python hello.py; python -c "from hello import greet; print(greet(\'World\'))"'
    shell.outputs = {
        "python hello.py": ("hello", 0),
        cmd.split("; ", 1)[1]: ("World", 0),
    }
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        result = svc.execute_local_step(
            tid="SCG-014",
            task={"worker_execution_context": {}},
            command=cmd,
            tool_calls=None,
            execution_policy=_policy(),
            guard_cfg={"execution_risk_policy": {"enabled": True, "task_scoped_only": False}},
        )
    assert result.exit_code == 0
    assert len(shell.commands) == 2
    chain_entry = next(
        (h for h in result.retry_history if h.get("normalization") == "command_chain_parsed"), None
    )
    assert chain_entry is not None
    assert chain_entry["segment_count"] == 2


# SCG-015: dangerous later segment must block all execution before first segment runs


def test_dangerous_second_segment_blocks_before_first_execution():
    """python hello.py; rm -rf /tmp/x → neither segment must execute."""
    svc = TaskExecutionService()
    shell = _StubShell()
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_deny_rm_risk),
    ):
        _, code, _, _, history = svc._execute_shell_command_with_policy(
            tid="SCG-015",
            command="python hello.py; rm -rf /tmp/x",
            execution_policy=_policy(),
            task={"worker_execution_context": {}},
            agent_cfg={"execution_risk_policy": {"enabled": True, "task_scoped_only": False}},
        )
    assert code == -1
    assert shell.commands == []
    chain_info = next((h.get("command_chain") for h in history if h.get("command_chain")), None)
    assert chain_info is not None
    denied_val = next((v for v in chain_info.get("validations", []) if not v.get("allowed")), None)
    assert denied_val is not None
    assert denied_val["segment_index"] == 2
    assert any("execution_risk_denied" in rc for rc in denied_val.get("reason_codes", []))


# SCG-016: unsupported shell operators stay blocked


def test_pipe_is_blocked():
    svc = TaskExecutionService()
    shell = _StubShell()
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        _, code, _, _, _ = svc._execute_shell_command_with_policy(
            tid="SCG-016-pipe",
            command="cat a | grep x",
            execution_policy=_policy(),
            task={"worker_execution_context": {}},
            agent_cfg={},
        )
    assert code == -1
    assert shell.commands == []


def test_command_substitution_is_blocked():
    svc = TaskExecutionService()
    shell = _StubShell()
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        _, code, _, _, _ = svc._execute_shell_command_with_policy(
            tid="SCG-016-subst",
            command="echo $(whoami)",
            execution_policy=_policy(),
            task={"worker_execution_context": {}},
            agent_cfg={},
        )
    assert code == -1
    assert shell.commands == []


def test_backtick_is_blocked():
    svc = TaskExecutionService()
    shell = _StubShell()
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        _, code, _, _, _ = svc._execute_shell_command_with_policy(
            tid="SCG-016-bt",
            command="echo `whoami`",
            execution_policy=_policy(),
            task={"worker_execution_context": {}},
            agent_cfg={},
        )
    assert code == -1
    assert shell.commands == []


def test_pipeline_mode_requires_policy_allow_complex_shell_mode():
    svc = TaskExecutionService()
    shell = _StubShell()
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        result = svc.execute_local_step(
            tid="SCG-CPLX-1",
            task={"worker_execution_context": {"shell_command_mode": "pipeline"}},
            command="cat a | grep x",
            tool_calls=None,
            execution_policy=_policy(),
            guard_cfg={
                "execution_risk_policy": {"enabled": True, "task_scoped_only": False},
                "shell_command_policy": {"allow_complex_shell_mode": False},
            },
        )
    assert result.exit_code == -1
    assert shell.commands == []


def test_pipeline_mode_allows_complex_shell_when_policy_enabled():
    svc = TaskExecutionService()
    shell = _StubShell()
    shell.outputs = {"cat a | grep x": ("ok", 0)}
    with (
        patch("agent.services.task_execution_result_handler.get_shell", return_value=shell),
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
    ):
        result = svc.execute_local_step(
            tid="SCG-CPLX-2",
            task={"worker_execution_context": {"shell_command_mode": "pipeline"}},
            command="cat a | grep x",
            tool_calls=None,
            execution_policy=_policy(),
            guard_cfg={
                "execution_risk_policy": {"enabled": True, "task_scoped_only": False},
                "shell_command_policy": {"allow_complex_shell_mode": True},
            },
        )
    assert result.exit_code == 0
    assert shell.commands == ["cat a | grep x"]
