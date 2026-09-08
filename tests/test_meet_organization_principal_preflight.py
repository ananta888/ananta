"""Parent preview uses actual SQL eligibility without Task or signing side effects."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from sqlmodel import Session, select

from agent.db_models import TaskDB
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_lifecycle import MeetDialogLifecycle
from agent.services.meet_organization_principal_preflight import MeetOrganizationPrincipalPreflight
from agent.services.meet_role_assignment import get_meet_role_assignments
from tests.meet_dialog_lifecycle_fixture import PUBLISHER, seed_parent
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_role_assignment_lifecycle import MUTATIONS, mutate

pytestmark = pytest.mark.timeout(45)
PARENT = "meet-test-parent"


def preflight_system(engine):
    seed_parent(engine)
    f = system()
    f.f.binding.require_write_access.reset_mock()
    f.tasks.organization_principals = True
    roles = get_meet_role_assignments()
    preflight = MeetOrganizationPrincipalPreflight(
        f.f.binding,
        MeetDialogLifecycle(f.tasks, role_assignments=roles),
        roles,
        publisher_url=PUBLISHER,
        issuer="https://synthetic-hub.invalid",
    )
    return f, preflight


def task_ids(engine):
    with Session(engine) as session:
        return list(session.exec(select(TaskDB.id).order_by(TaskDB.id)))


def test_parent_candidate_matches_later_admission_without_dispatch_or_signing(app):
    from agent.database import engine

    with app.app_context():
        f, preflight = preflight_system(engine)
        before = task_ids(engine)
        result = preflight.inspect(f.principal, "project", PARENT)
        assert set(result) == {"schema", "preflight_only", "issuer", "parent_task_id", "principal"}
        assert result["preflight_only"] is True and result["parent_task_id"] == PARENT
        assert result["schema"] == "ananta.meet-machine-principal-preflight.v1"
        assert result["issuer"] == preflight.issuer
        assert PUBLISHER not in str(result)
        assert task_ids(engine) == before
        f.f.binding.require_write_access.assert_called_once_with(f.principal, "project", PARENT)
        f.issuer.issue_dialog.assert_not_called()
        f.worker.start_dialog.assert_not_called()
        f.meet.join.assert_not_called()
        started = f.service.start(f.principal, "project", f.payload, parent=PARENT)
        task = f.tasks.get_by_id(started["task_id"])
        assert task.worker_execution_context["meet_machine_principal"] == result["principal"]


@pytest.mark.parametrize("change", [v for v in MUTATIONS if v not in {"incarnation", "policy_change"}])
def test_revoked_candidate_and_stale_preflight_cannot_start_a_dialog(app, change):
    from agent.database import engine

    with app.app_context():
        f, preflight = preflight_system(engine)
        assert preflight.inspect(f.principal, "project", PARENT)["preflight_only"] is True
        before = task_ids(engine)
        with Session(engine) as session:
            mutate(session, change)
        with pytest.raises(MeetError, match="assignment_denied"):
            preflight.inspect(f.principal, "project", PARENT)
        with pytest.raises(MeetError, match="assignment_denied"):
            f.service.start(f.principal, "project", f.payload, parent=PARENT)
        assert task_ids(engine) == before
        f.issuer.issue_dialog.assert_not_called()
        f.worker.start_dialog.assert_not_called()


@pytest.mark.parametrize("change", ["tenant", "project", "inactive", "archived", "unscoped", "no_role"])
def test_parent_scope_and_lifecycle_are_checked_before_candidate_derivation(app, change):
    from agent.database import engine

    with app.app_context():
        f, preflight = preflight_system(engine)
        with Session(engine) as session:
            row = session.get(TaskDB, PARENT)
            if change in {"tenant", "project"}:
                setattr(row, change + "_id", "foreign")
            elif change == "inactive":
                row.status = "cancelled"
            elif change == "archived":
                row.status = "archived"
            elif change == "unscoped":
                row.organization_id = row.unit_id = row.team_id = row.role_slot_id = None
            else:
                row.role_slot_id = None
            session.add(row)
            session.commit()
        with pytest.raises(MeetError):
            preflight.inspect(f.principal, "project", PARENT)
        f.worker.start_dialog.assert_not_called()


@pytest.mark.parametrize("change", ["worker", "service", "foreign_project", "access_denied", "empty_parent"])
def test_preflight_requires_user_task_write_authority_before_reading_role_rows(app, change):
    from agent.database import engine

    with app.app_context():
        f, preflight = preflight_system(engine)
        preflight.roles = Mock()
        principal, parent = f.principal, PARENT
        if change in {"worker", "service"}:
            principal = replace(principal, roles=frozenset({change}))
        elif change == "foreign_project":
            principal = replace(principal, project_id="foreign")
        elif change == "empty_parent":
            parent = ""
        else:
            f.f.binding.require_write_access.side_effect = MeetError("synthetic_access_denied", 403)
        with pytest.raises(MeetError):
            preflight.inspect(principal, "project", parent)
        preflight.roles.resolve.assert_not_called()
