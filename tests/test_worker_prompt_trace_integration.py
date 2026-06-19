from types import SimpleNamespace

from agent.services.task_scoped_execution_service import TaskScopedExecutionService


def test_forwarded_proposal_marks_uninspectable_without_prompt_trace(monkeypatch):
    captured = {}

    def _persist(**kwargs):
        captured.update(kwargs)

    # persist_forwarded_proposal lives in agent.services._task_scoped_forwarding,
    # so the get_core_services lookup must be patched there. Patching the facade
    # module does not affect the implementation that delegates to it.
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.get_core_services",
        lambda: SimpleNamespace(
            task_execution_service=SimpleNamespace(
                persist_task_proposal_result=_persist,
                build_task_history_event=lambda **k: {},
            ),
            autopilot_decision_service=SimpleNamespace(
                build_proposal_snapshot=lambda *_: {},
            ),
        ),
    )
    svc = TaskScopedExecutionService()
    response = {"backend": "external-worker", "command": "echo hi", "reason": "ok"}
    task = {"id": "t1", "goal_id": "g1"}
    svc._persist_forwarded_proposal(response, task, request_payload={"prompt": "hello"})
    trace = captured.get("trace") or {}
    assert trace.get("external_worker_uninspectable") is True
