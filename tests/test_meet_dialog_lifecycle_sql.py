"""Real ephemeral SQL/task writes, synthetic organization and owner policy."""

import pytest
from sqlmodel import Session

from agent.db_models import OrganizationInstanceDB, TaskDB
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_lifecycle import organization_tuple
from tests.meet_dialog_lifecycle_fixture import seed_parent
from tests.test_meet_dialog_avatar_negotiation import system

pytestmark = pytest.mark.timeout(45)


@pytest.mark.parametrize(
    "revocation", ["parent-cancel", "parent-archive", "organization-pause", "organization-archive"]
)
def test_real_scope_persists_and_revocation_preserves_cleanup(app, revocation):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        f = system()
        started = f.service.start(f.principal, "project", f.payload, parent="meet-test-parent")
        child = f.tasks.get_by_id(started["task_id"])
        parent = f.tasks.get_by_id("meet-test-parent")
        assert (
            organization_tuple(child)
            == organization_tuple(parent)
            == {
                "organization_id": "meet-test-org",
                "unit_id": "meet-test-unit",
                "team_id": "meet-test-team",
                "role_slot_id": "meet-test-slot",
            }
        )
        assert child.parent_task_id == parent.id
        context = child.worker_execution_context["meet_dialog"]
        f.f.authority.current(child.id, context["lease_id"], context["runtime_id"])
        wire = f.worker.start_dialog.call_args.args[0]
        assert "organization_id" not in wire  # No invented Meet principal or wire extension.
        with Session(engine) as session:
            if revocation.startswith("parent"):
                row = session.get(TaskDB, parent.id)
                row.status = "cancelled" if revocation == "parent-cancel" else "archived"
            else:
                row = session.get(OrganizationInstanceDB, "meet-test-org")
                row.lifecycle = "paused" if revocation == "organization-pause" else "archived"
            session.add(row)
            session.commit()
        with pytest.raises(MeetError, match="meet_dialog_(parent|organization)_inactive"):
            f.f.authority.current(child.id, context["lease_id"], context["runtime_id"])
        assert f.tasks.finish_bound(child.id, context["lease_id"], context["runtime_id"], "cancelled")
        assert f.tasks.get_by_id(child.id).status == "cancelled"
        assert not f.tasks.finish_bound(child.id, context["lease_id"], context["runtime_id"], "completed")


@pytest.mark.parametrize("failure", ["parent", "organization"])
def test_inactive_parent_or_organization_cannot_issue_a_grant_or_start_a_worker(app, failure):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        with Session(engine) as session:
            if failure == "parent":
                row = session.get(TaskDB, "meet-test-parent")
                row.status = "completed"
            else:
                row = session.get(OrganizationInstanceDB, "meet-test-org")
                row.lifecycle = "paused"
            session.add(row)
            session.commit()
        f = system()
        with pytest.raises(MeetError, match="meet_dialog_(parent|organization)_inactive"):
            f.service.start(f.principal, "project", f.payload, parent="meet-test-parent")
        f.issuer.issue_dialog.assert_not_called()
        f.worker.start_dialog.assert_not_called()
