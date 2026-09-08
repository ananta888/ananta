"""Headless terminal reporting and ordinary Hub deadline recovery after Worker loss."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.repositories.meet_dialog_deadlines import SqlDialogDeadlines
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_deadlines import MeetDialogDeadlines, original_deadline
from ananta_contracts.meet_dialog import validate_callback
from tests.test_dialog_browser_screen import Pool
from tests.test_meet_browser_hub_tasks import navigate, setup
from tests.test_meet_browser_workspace_contract import job
from worker.meet_media.browser_completion_reports import BrowserCompletionReports


def payload(f, job):
    return {
        "schema": "ananta.meet-dialog-callback.v1",
        "action": "browser_finish",
        "nonce": "a" * 32,
        "sent_at": f.f.now,
        **{key: f.assignment[key] for key in ("task_id", "lease_id", "runtime_id")},
        "browser_task_id": job["task_id"],
        "browser_lease_id": job["lease_id"],
        "status": "failed",
    }


def test_exact_signed_worker_result_finishes_child_without_requiring_live_publication_authority(app):
    with app.app_context():
        f = setup()
        browser_job, _ = navigate(f)
        report = payload(f, browser_job)
        assert validate_callback(report, f.f.now) is report
        f.browser.authority.current = Mock(side_effect=MeetError("synthetic_revocation", 403))
        assert f.service.browser_finish(report) == {"schema": "ananta.meet-browser-finished.v1", "nonce": "a" * 32}
        assert f.tasks.get_by_id(browser_job["task_id"]).status == "failed"
        assert f.tasks.get_by_id(f.task_id).status == "in_progress"
        f.browser.authority.current.assert_not_called()
        f.service.browser_finish(report)  # Idempotent cleanup does not revive/change terminal state.


@pytest.mark.parametrize("field", ["task_id", "lease_id", "runtime_id", "browser_task_id", "browser_lease_id"])
def test_borrowed_terminal_result_cannot_finish_other_parent_or_child(app, field):
    with app.app_context():
        f = setup()
        browser_job, _ = navigate(f)
        report = payload(f, browser_job) | {field: "other"}
        with pytest.raises(MeetError):
            f.service.browser_finish(report)
        assert f.tasks.get_by_id(browser_job["task_id"]).status == "in_progress"


def test_existing_hub_deadline_sweep_recovers_browser_after_process_or_hub_restart(app):
    from agent.database import engine
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f = setup()
        browser_job, _ = navigate(f)
        store = SqlDialogDeadlines(engine, task_status_cas=compare_and_set_local_task_status)
        candidate = next(row for row in store.page(None, 100) if row["task_id"] == browser_job["task_id"])
        assert original_deadline(candidate) == browser_job["deadline_ms"] / 1000
        runner = MeetDialogDeadlines(store, clock=lambda: browser_job["deadline_ms"] / 1000)
        assert runner.run_once()["settled"] == 1
        child = f.tasks.get_by_id(browser_job["task_id"])
        assert child.status == "failed"
        events = [row for row in child.history if row.get("event_type") == "meet_browser_deadline_expired"]
        assert len(events) == 1 and events[0]["details"] == {"reason": "original_deadline_expired"}
        assert f.tasks.get_by_id(f.task_id).status == "in_progress"


@pytest.mark.parametrize("field", ["task_id", "parent_task_id", "tenant_id", "project_id"])
def test_deadline_scanner_rejects_mismatched_job_projection(field):
    value = job()
    candidate = {
        "task_id": value["task_id"],
        "parent_task_id": value["parent_task_id"],
        "tenant_id": value["tenant_id"],
        "project_id": value["project_id"],
        "task_kind": "meet_browser_workspace",
        "context": {
            "meet_browser_job": value,
            "parent_dispatch": value["parent_lease_id"],
            "runtime_id": value["runtime_id"],
        },
    }
    candidate[field] = "other"
    with pytest.raises(ValueError, match="binding_invalid"):
        original_deadline(candidate)


def test_reporting_has_one_nonblocking_slot_and_no_retry_queue_or_content_fields():
    pool, hub = Pool(), Mock()
    reporter = BrowserCompletionReports(hub, pool=pool)
    first = job()
    reporter.report(first, "failed")
    reporter.report(first, "failed")
    reporter.report(deepcopy(first) | {"task_id": "next"}, "failed")
    assert len(pool.calls) == 1
    assert pool.calls[0][2:] == (
        ("browser_finish",),
        {"browser_task_id": first["task_id"], "browser_lease_id": first["lease_id"], "status": "failed"},
    )
    pool.calls[0][0].set_exception(ValueError("synthetic transport loss"))
    reporter.report(deepcopy(first) | {"task_id": "third"}, "failed")
    assert len(pool.calls) == 2
    reporter.close()
    reporter.close()
    pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "allow"},
        {"status": True},
        {"browser_task_id": ""},
        {"url": "https://example.com"},
        {"meet_session_id": "ms_" + "a" * 32},
    ],
)
def test_terminal_callback_shape_cannot_carry_navigation_or_grants(changes):
    value = {
        "schema": "ananta.meet-dialog-callback.v1",
        "action": "browser_finish",
        "nonce": "a" * 32,
        "sent_at": 100,
        "task_id": "parent",
        "lease_id": "dispatch",
        "runtime_id": "runtime",
        "browser_task_id": "child",
        "browser_lease_id": "child-lease",
        "status": "failed",
    }
    with pytest.raises(ValueError):
        validate_callback(value | changes, 100)
