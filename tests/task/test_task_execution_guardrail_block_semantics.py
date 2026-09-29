from __future__ import annotations

from types import SimpleNamespace

from agent.services.task_execution_service import TaskExecutionService


def test_guardrail_block_history_uses_blocked_status(monkeypatch) -> None:
    svc = TaskExecutionService()
    calls = {}

    def _fake_update(tid, status, **kwargs):
        calls["tid"] = tid
        calls["status"] = status
        calls["kwargs"] = kwargs

    # The wrapper on TaskExecutionService delegates to the module-level helper in
    # task_execution_result_handler. That helper resolves its runtime/audit
    # dependencies through its own module bindings, so the patch targets must
    # follow the delegation chain — patching the facade's module no longer
    # affects the implementation.
    monkeypatch.setattr(
        "agent.services.task_execution_result_handler.get_task_runtime_service",
        lambda: SimpleNamespace(update_local_task_status=_fake_update),
    )
    monkeypatch.setattr(
        "agent.services.task_execution_result_handler.get_execution_audit_service",
        lambda: SimpleNamespace(emit=lambda **_k: None),
    )
    decision = SimpleNamespace(blocked_tools=["bash"], reasons=["policy_denied"], details={"rule": "x"})
    svc._append_guardrail_block_history(
        "task-1",
        {"goal_id": "goal-1", "history": []},
        "rm -rf /",
        [],
        decision,
    )
    assert calls["status"] == "blocked"
    assert calls["kwargs"]["status_reason_code"] == "security_or_policy_denied"
