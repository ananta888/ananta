from agent.models import TaskExecutionPolicyContract
from agent.services.task_execution_service import TaskExecutionService


def _policy() -> TaskExecutionPolicyContract:
    return TaskExecutionPolicyContract(timeout_seconds=10, retries=0, retry_delay_seconds=0, source="test")


def test_unknown_tool_returns_needs_review_not_todo():
    service = TaskExecutionService()
    result = service.execute_local_step(
        tid="T-URI-1",
        task={"worker_execution_context": {"allowed_tools": ["file_read", "file_write"]}},
        command=None,
        tool_calls=[{"name": "hallucinated_tool", "args": {"text": "hello"}}],
        execution_policy=_policy(),
        guard_cfg={},
    )
    assert result.status == "needs_review"
    assert result.exit_code == 1
    assert result.failure_type == "tool_intent_unresolved_recoverable"
    assert result.retry_history and "reason_codes" in result.retry_history[0]

