from agent.models import TaskExecutionPolicyContract
from agent.services.task_execution_service import TaskExecutionService
from unittest.mock import patch


def _policy() -> TaskExecutionPolicyContract:
    return TaskExecutionPolicyContract(timeout_seconds=10, retries=0, retry_delay_seconds=0, source="test")


def test_unknown_tool_returns_needs_review_not_todo():
    service = TaskExecutionService()
    result = service.execute_local_step(
        tid="T-URI-1",
        task={"worker_execution_context": {"allowed_tools": ["file_read", "file_write"]}},
        command=None,
        tool_calls=[{"name": "hallucinated_tool", "args": {"command": "ls -la"}}],
        execution_policy=_policy(),
        guard_cfg={},
    )
    assert result.status == "needs_review"
    assert result.exit_code == 1
    assert result.failure_type == "tool_intent_unresolved_recoverable"
    assert result.retry_history and "reason_codes" in result.retry_history[0]


class _MissingBinaryShell:
    def execute(self, command: str, timeout: int = 30):
        return ("bash: line 1: gh: command not found", 127)


def test_missing_binary_shell_command_returns_needs_review():
    service = TaskExecutionService()
    # _execute_shell_command_with_policy was extracted into
    # task_execution_result_handler which resolves get_shell() from its own
    # module bindings, so the patch target must follow the extraction.
    with patch("agent.services.task_execution_result_handler.get_shell", return_value=_MissingBinaryShell()):
        result = service.execute_local_step(
            tid="T-URI-2",
            task={"worker_execution_context": {"allowed_tools": ["bash"]}},
            command="gh repo create test-x",
            tool_calls=[],
            execution_policy=_policy(),
            guard_cfg={},
        )
    assert result.status == "needs_review"
    assert result.exit_code == 127
