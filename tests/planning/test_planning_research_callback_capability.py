from types import SimpleNamespace

from agent.services import task_delegation_services
from agent.services.task_delegation_services import WorkerExecutionContextFactory


def _build_payload(monkeypatch, *, task_kind: str):
    issued = {}
    monkeypatch.setattr(
        task_delegation_services,
        "settings",
        SimpleNamespace(agent_url="http://hub:5000", port=5000, agent_name="hub"),
    )
    monkeypatch.setattr(
        task_delegation_services,
        "WorkerResultCapabilityService",
        lambda: SimpleNamespace(
            issue=lambda **kwargs: issued.update(kwargs) or "callback-token"
        ),
    )
    request = SimpleNamespace(
        task_id="parent-1",
        parent_task={
            "goal_id": "goal-1",
            "goal_trace_id": "trace-1",
            "team_id": "team-1",
        },
        data=SimpleNamespace(subtask_description="Research", priority="high"),
    )
    plan = SimpleNamespace(
        agent_url="http://worker:5000",
        effective_task_kind=task_kind,
        effective_required_capabilities=["planning"],
    )
    payload = WorkerExecutionContextFactory._delegation_payload(
        request=request,
        plan=plan,
        subtask_id="sub-1",
        context_bundle_id="ctx-1",
        retrieval_hints={
            "retrieval_intent": "category_research",
            "required_context_scope": "assignment",
            "preferred_bundle_mode": "catalog",
        },
        context_policy={},
        worker_execution_context={
            "task_proposal_binding": {"dispatch_lease_id": "lease-1"}
        },
    )
    return issued, payload


def test_planning_research_callback_capability_covers_long_running_execution(
    monkeypatch,
):
    issued, payload = _build_payload(monkeypatch, task_kind="planning_research")

    assert issued["ttl_seconds"] == 3600
    assert issued["assignment_id"] == "sub-1"
    assert payload["callback_token"] == "callback-token"


def test_regular_delegation_keeps_least_privilege_default_ttl(monkeypatch):
    issued, _payload = _build_payload(monkeypatch, task_kind="planning")

    assert "ttl_seconds" not in issued
