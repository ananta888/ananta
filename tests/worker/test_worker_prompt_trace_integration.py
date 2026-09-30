from types import SimpleNamespace

from agent.services.autopilot_decision_service import AutopilotDecisionService
from agent.services.task_scoped_execution_service import TaskScopedExecutionService
from tests.task_scoped_forwarding_seam import forwarding_dependencies  # noqa: F401 - pytest fixture


def test_forwarded_proposal_marks_uninspectable_without_prompt_trace(monkeypatch, forwarding_dependencies):
    captured = {}

    def _persist(**kwargs):
        captured.update(kwargs)

    # persist_forwarded_proposal receives core services through the
    # task-scoped forwarding dependency seam.
    dependencies = forwarding_dependencies(
        core_services=lambda: SimpleNamespace(
            task_execution_service=SimpleNamespace(
                persist_task_proposal_result=_persist,
                build_task_history_event=lambda **k: {},
            ),
            autopilot_decision_service=SimpleNamespace(
                build_proposal_snapshot=lambda *_: {},
                normalize_proposal_data=AutopilotDecisionService().normalize_proposal_data,
            ),
        ),
    )
    svc = TaskScopedExecutionService(forwarding_dependencies=dependencies)
    response = {"backend": "external-worker", "command": "echo hi", "reason": "ok"}
    task = {"id": "t1", "goal_id": "g1"}
    svc._persist_forwarded_proposal(response, task, request_payload={"prompt": "hello"})
    trace = captured.get("trace") or {}
    assert trace.get("external_worker_uninspectable") is True


def test_forwarded_proposal_keeps_the_executable_tool_calls(monkeypatch, forwarding_dependencies):
    """A worker may complete the task itself; the forwarded proposal record must hold the step it executed."""
    captured = {}
    dependencies = forwarding_dependencies(
        core_services=lambda: SimpleNamespace(
            task_execution_service=SimpleNamespace(
                persist_task_proposal_result=lambda **kwargs: captured.update(kwargs),
                build_task_history_event=lambda **k: {},
            ),
            autopilot_decision_service=AutopilotDecisionService(),
        ),
    )
    call = {"name": "final_answer", "args": {"answer": "## Ergebnis"}}
    response = {"backend": "orchestrator", "reason": "ok", "proposal": {"tool_calls": [call]}}
    TaskScopedExecutionService(forwarding_dependencies=dependencies)._persist_forwarded_proposal(response, {"id": "t2", "goal_id": "g"},
                                                              request_payload={"prompt": "p"})
    assert captured["tool_calls"] == [call]
