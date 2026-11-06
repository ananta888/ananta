"""Headless test-fix loop flow: lane queued, reports collected, fix rounds, green publish.

Mirrors the review workflow's test rig: real gate/registry doubles, fake queue capture
and a fake worker boundary via ``execute(service)`` from the execution tests.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent.services.self_development.contracts import form
from agent.services.self_development.policy import DevelopmentPolicy
from agent.services.self_development.test_fix_loop import write_test_report
from tests.self_development.test_execution import execute, runtime  # noqa: F401  (fixtures)


def _enable_loop(service, *, max_rounds: int = 3) -> None:
    value = dict(service.policy.value)
    value["test_fix_loop"] = {
        "enabled": True,
        "max_rounds": max_rounds,
        "minimum_profile": "self-development",
    }
    value["max_tasks"] = 5
    value["max_attempts"] = 3
    service.policy.path.write_text(json.dumps(value))
    service.policy = DevelopmentPolicy(service.policy.path)


def _lane_task(service, ingested):
    """Return (lane ingest record, dispatchable task object, carried workspace)."""
    lane = ingested[-1]
    workspace = service.policy.workspaces / ("self-dev-" + lane["task_id"])
    return lane, SimpleNamespace(id=lane["task_id"], status="todo"), workspace


def _answer_report(workspace, task_id, **report) -> None:
    payload = {"passed": False, "aborted_early": False, "ran": ["python -m pytest"], "expanded": [], "failures": []}
    payload.update(report)
    assert write_test_report(workspace, task_id=task_id, answer=json.dumps(payload)) is True


def test_execute_queues_the_adaptive_lane_instead_of_publishing(runtime):
    service, ingested, published = runtime
    service.import_tasks(form())
    service.start()
    _enable_loop(service)

    result = execute(service)

    assert result.completed is False
    assert result.failure_type == "development_awaiting_test_lane"
    item = service.status()["items"][0]
    assert item["status"] == "test_running"
    assert item["reason"] == "test_lane_round_1"
    assert published == []
    lane = ingested[-1]
    assert lane["extra_fields"]["task_kind"] == "verification"
    assert lane["priority"] == "high"
    # Without manages() the dispatcher would run the raw execute phase instead of
    # the lane collect (observed: pytest ran with cd /workspace and exited 4).
    assert service.manages(lane["task_id"]) is True
    assert lane["extra_fields"]["required_capabilities"] == ["testing"]
    assert lane["status"] == "todo"
    assert lane["event_details"]["test_lane_round"] == 1
    assert "Run exactly: python -m pytest" in lane["description"]
    # Snapshot semantics: carried workspace holds the worker's changes plus the marker,
    # and no per-attempt scaffolding from the development round leaks into it.
    workspace = service.policy.workspaces / ("self-dev-" + lane["task_id"])
    assert (workspace / "tests" / "self_development" / "test_example.py").is_file()
    assert (workspace / ".ananta" / "carried-baseline.json").is_file()
    assert not (workspace / ".ananta" / "mutation-report.json").exists()


def test_green_lane_runs_the_inner_propose_then_publishes(runtime):
    service, ingested, published = runtime
    service.import_tasks(form())
    service.start()
    _enable_loop(service)
    execute(service)
    lane, task, workspace = _lane_task(service, ingested)
    _answer_report(workspace, lane["task_id"], passed=True)
    inner_calls = []

    # The lane must invoke the inner dispatch: that is where the propose (and with
    # it the forced opencode runner that writes the report) actually runs.
    result = service.dispatch(
        task,
        SimpleNamespace(url="http://any-worker:5000"),
        lambda: inner_calls.append("executed"),
    )

    assert inner_calls == ["executed"]
    assert result.completed is True
    item = service.status()["items"][0]
    assert item["test_fix_rounds"] == 1
    assert item["test_fix_passed"] is True
    assert "test_task_id" not in item
    assert published == [item["task_id"]]
    assert item["status"] == "published"


def test_failing_report_starts_a_carried_fix_round_with_feedback(runtime):
    service, ingested, published = runtime
    service.import_tasks(form())
    service.start()
    _enable_loop(service)
    execute(service)
    lane, task, workspace = _lane_task(service, ingested)
    _answer_report(
        workspace,
        lane["task_id"],
        aborted_early=True,
        failures=[{"file": "tests/x/test_y.py", "test": "test_z", "error_tail": "AssertionError: boom"}],
    )

    result = service.dispatch(task, SimpleNamespace(url="http://any-worker:5000"), lambda: None)

    assert result.completed is True
    item = service.status()["items"][0]
    assert item["status"] == "ready"
    assert item["reason"] == "test_fix_round_1"
    assert "test_task_id" not in item
    assert item["test_fix_feedback"]["failures"][0]["file"] == "tests/x/test_y.py"
    assert published == []
    fix = ingested[-1]
    assert fix["extra_fields"]["task_kind"] == "coding"
    assert fix["event_details"]["test_fix_round"] == 1
    assert "Previous test-lane failures" in fix["description"]
    assert '"test": "test_z"' in fix["description"]
    fix_workspace = service.policy.workspaces / ("self-dev-" + fix["task_id"])
    assert (fix_workspace / "tests" / "self_development" / "test_example.py").is_file()
    assert (fix_workspace / ".ananta" / "carried-baseline.json").is_file()


def test_fix_round_then_green_lane_completes_the_full_loop(runtime):
    service, ingested, published = runtime
    service.import_tasks(form())
    service.start()
    _enable_loop(service)
    execute(service)
    lane, lane_task, lane_ws = _lane_task(service, ingested)
    _answer_report(lane_ws, lane["task_id"], failures=[{"file": "a.py", "test": "t", "error_tail": "e"}])
    service.dispatch(lane_task, SimpleNamespace(url="http://any-worker:5000"), lambda: None)

    # The fix round flows through the ordinary development dispatch (item ready, task ingested).
    fix_result = execute(service)
    assert fix_result.failure_type == "development_awaiting_test_lane"
    lane2, lane2_task, lane2_ws = _lane_task(service, ingested)
    assert lane2["task_id"] != lane["task_id"]
    _answer_report(lane2_ws, lane2["task_id"], passed=True)

    final = service.dispatch(lane2_task, SimpleNamespace(url="http://any-worker:5000"), lambda: None)

    assert final.completed is True
    item = service.status()["items"][0]
    assert item["test_fix_rounds"] == 2
    assert item["status"] == "published"
    assert published == [item["task_id"]]


def test_round_budget_exhausts_with_a_precise_reason(runtime):
    service, ingested, published = runtime
    service.import_tasks(form())
    service.start()
    _enable_loop(service, max_rounds=1)
    execute(service)
    lane, task, workspace = _lane_task(service, ingested)
    _answer_report(workspace, lane["task_id"], failures=[{"file": "a.py", "test": "t", "error_tail": "e"}])

    result = service.dispatch(task, SimpleNamespace(url="http://any-worker:5000"), lambda: None)

    assert result.failed is True
    assert result.failure_type == "test_fix_rounds_exhausted"
    item = service.status()["items"][0]
    assert item["status"] == "failed"
    assert item["reason"] == "test_fix_rounds_exhausted"
    assert item["test_fix_rounds"] == 1
    assert published == []


def test_missing_report_mints_a_fresh_lane_task_twice_then_fails(runtime, monkeypatch):
    service, ingested, published = runtime
    service.import_tasks(form())
    service.start()
    _enable_loop(service)
    execute(service)
    lane, task, _workspace = _lane_task(service, ingested)
    status_calls = []
    monkeypatch.setattr(
        "agent.services.self_development.coordinator.update_local_task_status",
        lambda *args, **kwargs: status_calls.append((args, kwargs)),
    )

    def item():
        return service.status()["items"][0]

    # Strike 1: a fresh lane task id is minted (the stale pin/proposal of the old one
    # would let the dispatcher skip a new propose entirely).
    first = service.dispatch(task, SimpleNamespace(url="http://any-worker:5000"), lambda: None)
    assert first.failure_type == "test_report_pending_retry"
    assert item()["status"] == "test_running"
    assert item()["test_report_retries"] == 1
    assert item()["test_task_id"] != lane["task_id"]
    assert ingested[-1]["task_id"] == item()["test_task_id"]
    assert ingested[-1]["priority"] == "high"
    assert ingested[-1]["extra_fields"]["task_kind"] == "verification"
    # The replacement vehicle carries the tested snapshot (runner-visible tests).
    minted_ws = service.policy.workspaces / ("self-dev-" + item()["test_task_id"])
    assert (minted_ws / "tests" / "self_development" / "test_example.py").is_file()
    assert (minted_ws / ".ananta" / "carried-baseline.json").is_file()
    assert any(args[1] == "failed" and args[0] == lane["task_id"] for args, _kw in status_calls)

    # Strike 2: dispatched on the NEW id.
    second_task = SimpleNamespace(id=item()["test_task_id"], status="todo")
    second = service.dispatch(second_task, SimpleNamespace(url="http://any-worker:5000"), lambda: None)
    assert second.failure_type == "test_report_pending_retry"
    assert item()["test_report_retries"] == 2

    # Strike 3: bounded, precise failure.
    third_task = SimpleNamespace(id=item()["test_task_id"], status="todo")
    third = service.dispatch(third_task, SimpleNamespace(url="http://any-worker:5000"), lambda: None)
    assert third.failure_type == "test_report_missing"
    assert item()["status"] == "failed"
    assert item()["reason"] == "test_report_missing"
    assert published == []


def test_green_lane_stages_the_acceptance_review_when_required(runtime):
    from tests.self_development.test_acceptance_review import _enable_independent_review

    service, ingested, published = runtime
    service.import_tasks(form())
    service.start()
    _enable_loop(service)
    execute(service)
    _enable_independent_review(service)
    lane, task, workspace = _lane_task(service, ingested)
    _answer_report(workspace, lane["task_id"], passed=True)

    service.dispatch(task, SimpleNamespace(url="http://any-worker:5000"), lambda: None)

    item = service.status()["items"][0]
    # The test double reviewer runs synchronously (no enqueue_review), so the green
    # lane goes straight through review into publication -- identical to the direct path.
    assert item["status"] == "published"
    assert published == [item["task_id"]]
    assert item.get("acceptance_review") is not None
