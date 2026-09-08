"""Actual Hub SQL assignment revocation, with explicitly synthetic registration."""

import pytest
from sqlmodel import Session

from agent.db_models import AgentInfoDB, OrganizationRoleAssignmentDB, OrganizationRoleSlotDB, TaskDB
from agent.services.meet_contract import MeetError
from tests.meet_dialog_lifecycle_fixture import PUBLISHER, seed_parent
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_media_child_scope import organized_dialog

pytestmark = pytest.mark.timeout(45)


def test_current_dialog_rejects_suspended_exact_role_assignment(app):
    from agent.database import engine

    with app.app_context():
        f, dialog, scope, _ = organized_dialog(engine)
        with Session(engine) as session:
            row = session.get(OrganizationRoleAssignmentDB, "meet-test-assignment")
            row.lifecycle = "suspended"
            session.add(row)
            session.commit()
        with pytest.raises(MeetError, match="meet_dialog_assignment"):
            f.f.authority.current(dialog.id, scope.lease_id, scope.runtime_id)


MUTATIONS = [
    "suspended",
    "ended",
    "proposed",
    "incarnation",
    "ended_at",
    "offline",
    "unvalidated",
    "capability",
    "worker_role",
    "principal_kind",
    "slot_capability",
    "forbidden",
    "write_access",
    "policy_change",
]


def mutate(session, change):
    if change in {"suspended", "ended", "proposed", "incarnation", "ended_at"}:
        row = session.get(OrganizationRoleAssignmentDB, "meet-test-assignment")
        if change == "incarnation":
            row.assigned_at += 1
        elif change == "ended_at":
            row.ended_at = 2000.0
        else:
            row.lifecycle = change
    elif change in {"offline", "unvalidated", "capability", "worker_role"}:
        row = session.get(AgentInfoDB, PUBLISHER)
        if change == "offline":
            row.status = "offline"
        elif change == "unvalidated":
            row.registration_validated = False
        elif change == "worker_role":
            row.role = "hub"
        else:
            row.authorized_capabilities = []
            row.capabilities = ["meet_dialog_session"]  # Legacy list cannot restore revoked rights.
    else:
        row = session.get(OrganizationRoleSlotDB, "meet-test-slot")
        row.assignment_policy = (
            row.assignment_policy
            | {
                "principal_kind": {"principal_kinds": ["user"]},
                "slot_capability": {"required_capabilities": ["not_authorized"]},
                "forbidden": {"forbidden_capabilities": ["meet_dialog_session"]},
                "write_access": {"write_access_required": True},
                "policy_change": {"principal_kinds": ["agent", "human"]},
            }[change]
        )
    session.add(row)
    session.commit()


@pytest.mark.parametrize("change", MUTATIONS)
def test_authority_and_child_admission_reject_changed_registration_assignment_or_policy(app, change):
    from agent.database import engine
    from agent.services.meet_turn_service import HubMediaTasks
    from tests.test_meet_media import turn

    with app.app_context():
        f, dialog, scope, audio = organized_dialog(engine)
        binding = dialog.worker_execution_context["meet_role_assignment"]
        assert dialog.assigned_agent_url == PUBLISHER
        assert binding["assignment_id"] == "meet-test-assignment"
        wire = f.worker.start_dialog.call_args.args[0]
        assert "meet_role_assignment" not in wire and "publisher_url" not in wire
        with Session(engine) as session:
            mutate(session, change)
        f.f.binding.read.reset_mock()
        with pytest.raises(MeetError, match="^meet_dialog_assignment_revoked$"):
            f.f.authority.current(dialog.id, scope.lease_id, scope.runtime_id)
        f.f.binding.read.assert_not_called()
        with pytest.raises(MeetError, match="^meet_dialog_assignment_revoked$"):
            f.service.audio_coordinator.start(audio)
        with pytest.raises(MeetError, match="^meet_dialog_assignment_revoked$"):
            HubMediaTasks().start(turn() | {"binding_task_id": dialog.id}, "owner")
        assert f.tasks.finish_bound(dialog.id, scope.lease_id, scope.runtime_id, "cancelled")


@pytest.mark.parametrize(
    "change", [v for v in MUTATIONS if v not in {"incarnation", "policy_change"}] + ["missing", "foreign_worker"]
)
def test_ineligible_assignment_never_issues_grant_or_dispatches(app, change):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        f = system()
        with Session(engine) as session:
            if change == "missing":
                session.delete(session.get(OrganizationRoleAssignmentDB, "meet-test-assignment"))
                session.commit()
            elif change == "foreign_worker":
                f.tasks.publisher_url = "http://another-worker:8091"
            else:
                mutate(session, change)
        with pytest.raises(MeetError, match="^meet_dialog_assignment_denied$"):
            f.service.start(f.principal, "project", f.payload, parent="meet-test-parent")
        f.issuer.issue_dialog.assert_not_called()
        f.worker.start_dialog.assert_not_called()


@pytest.mark.parametrize(
    "field",
    [
        "missing",
        "extra",
        "task_id",
        "parent_task_id",
        "lease_id",
        "runtime_id",
        "scope",
        "assignment_id",
        "publisher_url",
        "snapshot_digest",
        "assigned_agent_url",
    ],
)
def test_closed_persisted_binding_cannot_be_repaired_or_retargeted(app, field):
    from copy import deepcopy

    from agent.database import engine

    with app.app_context():
        f, dialog, scope, _ = organized_dialog(engine)
        with Session(engine) as session:
            row = session.get(TaskDB, dialog.id)
            execution = deepcopy(row.worker_execution_context)
            if field == "missing":
                execution.pop("meet_role_assignment")
            elif field == "assigned_agent_url":
                session.add(AgentInfoDB(url="http://another-worker:8091", name="Other synthetic worker"))
                session.commit()
                row.assigned_agent_url = "http://another-worker:8091"
            else:
                execution["meet_role_assignment"][field] = "replacement"
            row.worker_execution_context = execution
            session.add(row)
            session.commit()
        with pytest.raises(MeetError, match="^meet_dialog_assignment_revoked$"):
            f.f.authority.current(dialog.id, scope.lease_id, scope.runtime_id)
