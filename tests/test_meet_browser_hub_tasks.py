"""Real Hub Task persistence, parent lifecycle and independent browser controls."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.services.meet_browser_policy import MeetBrowserPolicy
from agent.services.meet_browser_tasks import HubBrowserTasks
from agent.services.meet_browser_workspaces import MeetBrowserWorkspaces
from agent.services.meet_contract import MeetError
from agent.services.source_control_access_policy import HubSourcePrincipal
from tests.test_meet_dialog_avatar_negotiation import system


def setup():
    from sqlmodel import Session, select

    from agent.database import engine
    from agent.db_models import ProjectDB

    # Real FK-backed child Tasks require an admitted project; legacy dialog
    # policy mocks by themselves are not a persisted organization fixture.
    with Session(engine) as session:
        if (
            session.exec(
                select(ProjectDB).where(ProjectDB.tenant_id == "tenant", ProjectDB.project_id == "project")
            ).first()
            is None
        ):
            session.add(
                ProjectDB(
                    tenant_id="tenant", project_id="project", name="Synthetic browser", created_by_subject_id="owner"
                )
            )
            session.commit()
    f = system()
    f.f.authority.policies[("tenant", "project")] |= {"screen.publish"}
    f.policy_row = {
        "tenant_id": "tenant",
        "project_id": "project",
        "owner_subject": "owner",
        "policy_id": "public-docs",
        "revision": 1,
        "allowed_origins": ["https://example.com"],
        "operations": ["navigate", "present"],
    }
    f.browser_tasks = HubBrowserTasks(f.tasks)
    f.browser = MeetBrowserWorkspaces(
        f.f.authority, f.browser_tasks, MeetBrowserPolicy([f.policy_row]), clock=lambda: f.f.now
    )
    f.service.browser_workspaces = f.browser
    started = f.service.start(
        f.principal, "project", f.payload | {"capabilities": ["screen.publish"], "browser_workspace": True}
    )
    f.task_id = started["task_id"]
    f.assignment = f.worker.start_dialog.call_args.args[0]
    f.ids = tuple(f.assignment[k] for k in ("task_id", "lease_id", "runtime_id"))
    f.scope = f.f.authority.current(*f.ids)
    f.receipt = {
        "lease": {"sessionId": "ms_" + "a" * 32, "generation": 1, "expiresAt": (f.f.now + 120) * 1000},
        "peerId": "machine",
        "roomId": f.scope.room_id,
        "membershipEpoch": 2,
    }
    return f


def navigate(f, expected=1):
    result = f.browser.change(
        f.principal,
        "project",
        f.task_id,
        {"action": "navigate", "expected_revision": expected, "url": "https://example.com/docs"},
    )
    return f.browser_tasks.read(f.scope)["job"], result


def present(f, expected=2):
    return f.browser.change(f.principal, "project", f.task_id, {"action": "present", "expected_revision": expected})


def test_navigation_creates_ordinary_child_task_without_presenting_or_dispatching_other_worker(app):
    with app.app_context():
        f = setup()
        job, result = navigate(f)
        parent, child = f.tasks.get_by_id(f.task_id), f.tasks.get_by_id(job["task_id"])
        assert child.task_kind == "meet_browser_workspace" and child.status == "in_progress"
        assert child.parent_task_id == parent.id and child.assigned_agent_url == parent.assigned_agent_url
        assert (child.tenant_id, child.project_id) == (parent.tenant_id, parent.project_id)
        assert child.worker_execution_context == {
            "meet_browser_job": job,
            "parent_dispatch": f.scope.lease_id,
            "runtime_id": f.scope.runtime_id,
        }
        assert job["deadline_ms"] - job["issued_at_ms"] == 30000
        assert result["mode"] == "off" and result["revision"] == 2
        assert "url" not in result and "fetch" not in result
        before = f.browser.projection(f.scope, f.receipt)
        assert before["mode"] == "off" and before["job"] == job
        present(f)
        after = f.browser.projection(f.scope, f.receipt)
        assert after["mode"] == "browser" and after["job"] == job and after["revision"] == 3
        assert before["binding"] == after["binding"]
        assert (
            f.tasks.get_by_id(f.task_id).worker_execution_context["meet_dialog"]["controls"]
            == parent.worker_execution_context["meet_dialog"]["controls"]
        )
        f.worker.start_dialog.assert_called_once()


def test_stale_cas_or_missing_job_never_creates_additional_task(app):
    with app.app_context():
        f = setup()
        with pytest.raises(MeetError, match="task_required"):
            present(f, expected=1)
        job, _ = navigate(f)
        f.browser_tasks.create = Mock(wraps=f.browser_tasks.create)
        with pytest.raises(MeetError, match="control_conflict"):
            navigate(f, expected=1)
        f.browser_tasks.create.assert_not_called()
        assert f.browser_tasks.read(f.scope)["job"] == job


def test_actual_parent_cas_has_one_winner_and_keeps_other_context_fields(app):
    with app.app_context():
        f = setup()
        old = f.browser_tasks.read(f.scope)
        before = deepcopy(f.tasks.get_by_id(f.task_id).worker_execution_context)
        assert f.browser_tasks.replace(f.scope, old, old | {"revision": 2, "mode": "off"})
        assert not f.browser_tasks.replace(f.scope, old, old | {"revision": 2, "mode": "status"})
        after = f.tasks.get_by_id(f.task_id).worker_execution_context
        assert {k: v for k, v in after.items() if k != "meet_browser"} == before


@pytest.mark.parametrize("action", ["stop", "status", "navigate"])
def test_new_navigation_or_explicit_stop_terminates_old_browser_task_only(app, action):
    with app.app_context():
        f = setup()
        old, _ = navigate(f)
        if action == "navigate":
            new, _ = navigate(f, expected=2)
            assert new["task_id"] != old["task_id"] and new["page_id"] != old["page_id"]
            assert new["navigation_revision"] == 3
        else:
            f.browser.change(f.principal, "project", f.task_id, {"action": action, "expected_revision": 2})
            assert f.browser_tasks.read(f.scope)["job"] is None
        assert f.tasks.get_by_id(old["task_id"]).status == "cancelled"
        assert f.tasks.get_by_id(f.task_id).status == "in_progress"


@pytest.mark.parametrize("failure", ["policy_revision", "policy_removed", "browser_completed", "expiry", "room"])
def test_fresh_exchange_blocks_stale_or_terminal_browser_without_status_fallback(app, failure):
    with app.app_context():
        f = setup()
        job, _ = navigate(f)
        present(f)
        if failure == "policy_revision":
            f.browser.policy = MeetBrowserPolicy([f.policy_row | {"revision": 2}])
        elif failure == "policy_removed":
            f.browser.policy = MeetBrowserPolicy([])
        elif failure == "browser_completed":
            assert f.browser_tasks.finish(f.scope, job, "completed")
        elif failure == "expiry":
            f.f.now += 31
        else:
            f.receipt["roomId"] = "other"
        result = f.browser.projection(f.scope, f.receipt)
        assert result["mode"] == "off" and result["job"] is None and result["binding"] is None
        assert result["reason"] in {"policy_denied", "task_inactive", "expired"}
        assert f.tasks.get_by_id(job["task_id"]).status in {"completed", "cancelled", "failed"}
        assert f.tasks.get_by_id(f.task_id).status == "in_progress"


@pytest.mark.parametrize("field,value", [("tenant_id", "foreign"), ("project_id", "foreign"), ("subject_id", "viewer")])
def test_meet_viewer_or_foreign_principal_cannot_control_browser(app, field, value):
    with app.app_context():
        f = setup()
        fields = {"subject_id": "owner", "tenant_id": "tenant", "project_id": "project", "roles": frozenset({"user"})}
        fields[field] = value
        principal = HubSourcePrincipal(**fields)
        with pytest.raises(MeetError):
            f.browser.change(
                principal,
                principal.project_id,
                f.task_id,
                {"action": "navigate", "expected_revision": 1, "url": "https://example.com"},
            )
        assert f.browser_tasks.read(f.scope)["revision"] == 1


def test_missing_presentation_policy_never_upgrades_navigation_to_publication(app):
    with app.app_context():
        f = setup()
        f.browser.policy = MeetBrowserPolicy([f.policy_row | {"operations": ["navigate"]}])
        job, _ = navigate(f)
        with pytest.raises(MeetError, match="policy_denied"):
            present(f)
        assert f.browser.projection(f.scope, f.receipt)["mode"] == "off"
        assert f.tasks.get_by_id(job["task_id"]).status == "in_progress"


def test_failed_or_uncertain_child_creation_cannot_leave_an_executable_reservation(app):
    with app.app_context():
        f = setup()
        actual = f.browser_tasks.create

        def uncertain(scope, job):
            actual(scope, job)
            raise RuntimeError("synthetic uncertainty")

        f.browser_tasks.create = uncertain
        with pytest.raises(MeetError, match="dispatch_failed"):
            navigate(f)
        job = f.browser_tasks.read(f.scope)["job"]
        assert f.tasks.get_by_id(job["task_id"]).status == "failed"
        assert f.browser.projection(f.scope, f.receipt)["job"] is None


def test_parent_completion_cancels_current_browser_child(app):
    with app.app_context():
        f = setup()
        job, _ = navigate(f)
        assert f.service.inspect(f.principal, "project", f.task_id, stop=True)["status"] == "cancelled"
        assert f.tasks.get_by_id(job["task_id"]).status == "cancelled"
        with pytest.raises(MeetError):
            f.browser.projection(f.scope, f.receipt)


@pytest.mark.parametrize("change", ["job", "parent", "worker", "revive"])
def test_browser_task_identity_and_terminality_cannot_be_rewritten_via_normal_repository(app, change):
    from agent.services.repository_registry import get_repository_registry

    with app.app_context():
        f = setup()
        job, _ = navigate(f)
        if change == "revive":
            assert f.browser_tasks.finish(f.scope, job, "completed")
        child = f.tasks.get_by_id(job["task_id"])
        if change == "job":
            child.worker_execution_context["meet_browser_job"]["fetch"]["url"] = "https://example.com/other"
        elif change == "parent":
            child.parent_task_id = "other"
        elif change == "worker":
            child.assigned_agent_url = "https://other.example"
        else:
            child.status = "in_progress"
        with pytest.raises(ValueError, match="meet_browser_(?:task_identity|terminal)_immutable"):
            get_repository_registry().task_repo.save(child)
