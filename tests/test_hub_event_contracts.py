from agent.common.audit import log_audit
from agent.db_models import GoalDB, TaskDB
from agent.repository import audit_repo, goal_repo, task_repo
from agent.services.hub_event_service import build_hub_event_catalog
from agent.services.task_runtime_service import append_task_history_event


def test_hub_event_catalog_exposes_versioned_channels():
    catalog = build_hub_event_catalog().model_dump()

    assert catalog["version"] == "v1"
    assert "task_history" in catalog["channels"]
    assert "governance" in catalog["channels"]
    assert catalog["channels"]["audit"] == ["*"]


def test_append_task_history_event_writes_versioned_hub_event():
    task = TaskDB(id="hub-event-task", status="todo", goal_id="goal-1", goal_trace_id="trace-1", plan_id="plan-1")

    append_task_history_event(task, "task_ingested", actor="system", details={"source": "api"})

    history = task.history or []
    assert len(history) == 1
    event = history[0]
    assert event["version"] == "v1"
    assert event["kind"] == "hub_event"
    assert event["channel"] == "task_history"
    assert event["event_type"] == "task_ingested"
    assert event["context"]["task_id"] == "hub-event-task"
    assert event["context"]["goal_id"] == "goal-1"
    assert event["context"]["trace_id"] == "trace-1"
    assert event["context"]["plan_id"] == "plan-1"
    assert event["details"]["source"] == "api"


def test_audit_log_persists_hub_event_metadata():
    goal = goal_repo.save(GoalDB(goal="Audit event goal", summary="Audit event goal", status="planned"))
    task_repo.save(TaskDB(id="audit-event-task", title="Task", status="todo", goal_id=goal.id, goal_trace_id=goal.trace_id))

    log_audit(
        "policy_decision_recorded",
        {"task_id": "audit-event-task", "goal_id": goal.id, "trace_id": goal.trace_id, "token": "secret"},
    )

    logs = audit_repo.get_all(limit=5)
    assert logs
    details = logs[0].details or {}
    assert details["token"] == "***REDACTED***"
    assert details["_event"]["version"] == "v1"
    assert details["_event"]["kind"] == "hub_event"
    assert details["_event"]["channel"] == "audit"
    assert details["_event"]["event_type"] == "policy_decision_recorded"
    assert details["_event"]["context"]["task_id"] == "audit-event-task"
