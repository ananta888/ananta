"""Real Hub SQL/TaskQueue identity and routing; execution ports are explicitly synthetic."""

from unittest.mock import Mock

import pytest
from sqlmodel import Session

from agent.db_models import OrganizationRoleAssignmentDB
from agent.repositories.meet_role_assignment import SqlMeetRoleAssignments
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_lifecycle import MeetDialogLifecycle
from agent.services.meet_dialog_publishers import MeetDialogPublishers
from agent.services.meet_dialog_worker_router import MeetDialogWorkerRouter
from agent.services.meet_organization_principal_preflight import MeetOrganizationPrincipalPreflight
from agent.services.meet_role_assignment import MeetRoleAssignments
from tests.meet_dialog_lifecycle_fixture import PUBLISHER
from tests.meet_multi_role_fixture import PARENTS, SECOND_PUBLISHER, seed_multi_role_parents
from tests.test_meet_dialog_avatar_negotiation import system

pytestmark = pytest.mark.timeout(45)


def test_two_parent_roles_dispatch_separately_and_revocation_is_not_a_fallback(app):
    from agent.database import engine

    with app.app_context():
        seed_multi_role_parents(engine)
        f = system()
        second = Mock()
        roles = MeetRoleAssignments(SqlMeetRoleAssignments())
        selection = MeetDialogPublishers(roles.rows, [PUBLISHER, SECOND_PUBLISHER], PUBLISHER)
        f.tasks.publishers = selection
        f.tasks.organization_principals = True
        f.service.worker = MeetDialogWorkerRouter(
            f.f.authority, f.tasks, {PUBLISHER: f.worker, SECOND_PUBLISHER: second}, PUBLISHER, clock=lambda: f.f.now
        )
        preflight = MeetOrganizationPrincipalPreflight(
            f.f.binding,
            MeetDialogLifecycle(f.tasks),
            roles,
            publisher_url=PUBLISHER,
            issuer="https://synthetic.example.test",
            publishers=selection,
        )
        expected = [preflight.inspect(f.principal, "project", parent)["principal"] for parent in PARENTS]
        started = [f.service.start(f.principal, "project", f.payload, parent=parent) for parent in PARENTS]
        assert expected[0]["subject"] != expected[1]["subject"]
        rows = [f.tasks.get_by_id(item["task_id"]) for item in started]
        for index, (row, worker) in enumerate(zip(rows, [f.worker, second], strict=True)):
            assert row.parent_task_id == PARENTS[index]
            assert row.assigned_agent_url == [PUBLISHER, SECOND_PUBLISHER][index]
            assert row.worker_execution_context["meet_machine_principal"] == expected[index]
            worker.start_dialog.assert_called_once()
            assert worker.start_dialog.call_args.args[0]["task_id"] == row.id
        with Session(engine) as session:
            assignment = session.get(OrganizationRoleAssignmentDB, "meet-test-assignment-second")
            assignment.lifecycle = "suspended"
            session.add(assignment)
            session.commit()
        with pytest.raises(MeetError, match="assignment_revoked"):
            f.service.worker.start_dialog(second.start_dialog.call_args.args[0])
        with pytest.raises(MeetError, match="publisher_selection_denied"):
            preflight.inspect(f.principal, "project", PARENTS[1])
        with pytest.raises(MeetError, match="publisher_selection_denied"):
            f.service.start(f.principal, "project", f.payload, parent=PARENTS[1])
        first = rows[0].worker_execution_context["meet_dialog"]
        assert (
            f.f.authority.current(rows[0].id, first["lease_id"], first["runtime_id"]).machine_subject
            == expected[0]["subject"]
        )
        f.worker.start_dialog.assert_called_once()
        second.start_dialog.assert_called_once()


@pytest.mark.parametrize("case", ["ambiguous", "revoked_after_selection"])
def test_sql_selection_failure_never_ingests_or_dispatches_a_task(app, monkeypatch, case):
    from sqlmodel import select

    from agent.database import engine
    from agent.db_models import TaskDB

    with app.app_context():
        seed_multi_role_parents(engine)
        f = system()
        selection = MeetDialogPublishers(SqlMeetRoleAssignments(), [PUBLISHER, SECOND_PUBLISHER], PUBLISHER)
        f.tasks.publishers = selection
        if case == "ambiguous":
            with Session(engine) as session:
                session.add(
                    OrganizationRoleAssignmentDB(
                        id="synthetic-conflicting-assignment",
                        tenant_id="tenant",
                        project_id="project",
                        organization_id="meet-test-org",
                        role_slot_id="meet-test-slot",
                        agent_url=SECOND_PUBLISHER,
                        lifecycle="active",
                        assigned_at=1000.0,
                    )
                )
                session.commit()
        else:
            original = selection.select

            def revoke(*args):
                target = original(*args)
                with Session(engine) as session:
                    row = session.get(OrganizationRoleAssignmentDB, "meet-test-assignment")
                    row.lifecycle = "suspended"
                    session.add(row)
                    session.commit()
                return target

            monkeypatch.setattr(selection, "select", revoke)
        with pytest.raises(MeetError):
            f.service.start(f.principal, "project", f.payload, parent=PARENTS[0])
        f.worker.start_dialog.assert_not_called()
        f.issuer.issue_dialog.assert_not_called()
        with Session(engine) as session:
            assert session.exec(select(TaskDB).where(TaskDB.task_kind == "meet_dialog_session")).all() == []
