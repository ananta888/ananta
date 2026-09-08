"""Exact-scope SQL reads and live leaf transitions, without disabling foreign keys."""

from dataclasses import replace

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from agent.db_models import OrganizationRoleSlotDB, OrganizationTeamLinkDB, OrganizationUnitDB
from agent.models.meet_organization_topology import MeetTopologyScope, MeetTopologySnapshot
from agent.repositories.meet_organization_topology import SqlMeetOrganizationTopology
from agent.services.meet_contract import MeetError
from agent.services.meet_organization_topology import active_topology
from tests.meet_dialog_lifecycle_fixture import seed_parent
from tests.test_meet_dialog_avatar_negotiation import system

pytestmark = pytest.mark.timeout(45)
SCOPE = MeetTopologyScope("tenant", "project", "meet-test-org", "meet-test-unit", "meet-test-team", "meet-test-slot")


def leaf_row(session, leaf):
    if leaf == "unit":
        return session.get(OrganizationUnitDB, SCOPE.unit_id)
    if leaf == "role":
        return session.get(OrganizationRoleSlotDB, SCOPE.role_slot_id)
    return session.exec(
        select(OrganizationTeamLinkDB).where(
            OrganizationTeamLinkDB.organization_id == SCOPE.organization_id,
            OrganizationTeamLinkDB.team_id == SCOPE.team_id,
        )
    ).one()


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "organization_id"])
def test_foreign_scope_exposes_no_leaf_metadata_including_global_team_flag(app, field):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        rows = SqlMeetOrganizationTopology(lambda: Session(engine))
        assert active_topology(SCOPE, rows.read(SCOPE))
        assert rows.read(replace(SCOPE, **{field: "foreign"})) == MeetTopologySnapshot()


@pytest.mark.parametrize("field", ["unit_id", "team_id", "role_slot_id"])
def test_missing_referenced_leaf_is_not_implicitly_active(app, field):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        scope = replace(SCOPE, **{field: "missing"})
        assert not active_topology(scope, SqlMeetOrganizationTopology().read(scope))


@pytest.mark.parametrize("leaf", ["unit", "link", "role"])
@pytest.mark.parametrize("status", ["planned", "draining", "archived"])
def test_nonactive_sql_leaf_prevents_grant_and_worker_dispatch(app, leaf, status):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        with Session(engine) as session:
            row = leaf_row(session, leaf)
            row.lifecycle = status
            session.add(row)
            session.commit()
        assert not active_topology(SCOPE, SqlMeetOrganizationTopology().read(SCOPE))
        f = system()
        with pytest.raises(MeetError, match="^meet_dialog_organization_inactive$"):
            f.service.start(f.principal, "project", f.payload, parent="meet-test-parent")
        f.issuer.issue_dialog.assert_not_called()
        f.worker.start_dialog.assert_not_called()


@pytest.mark.parametrize("leaf", ["link", "role"])
def test_individually_valid_foreign_keys_do_not_authorize_mixed_unit_assignment(app, leaf):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        with Session(engine) as session:
            session.add(
                OrganizationUnitDB(
                    id="other-unit",
                    tenant_id=SCOPE.tenant_id,
                    project_id=SCOPE.project_id,
                    organization_id=SCOPE.organization_id,
                    unit_key="other",
                    name="Other synthetic unit",
                    unit_kind="team",
                    lifecycle="active",
                )
            )
            session.commit()
            row = leaf_row(session, leaf)
            row.unit_id = "other-unit"
            session.add(row)
            session.commit()
        # This is legal under the individual SQL foreign keys, but not under
        # the task's exact unit/role/team combination.
        assert not active_topology(SCOPE, SqlMeetOrganizationTopology().read(SCOPE))


def test_topology_reader_has_four_bounded_metadata_queries_and_no_role_policy_load(app):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        statements = []

        def observed(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", observed)
        try:
            state = SqlMeetOrganizationTopology().read(SCOPE)
        finally:
            event.remove(engine, "before_cursor_execute", observed)
        assert active_topology(SCOPE, state)
        assert len(statements) == 4
        assert all(statement.lstrip().startswith("SELECT") and "LIMIT" in statement for statement in statements)
        assert all(
            "assignment_policy" not in statement and "separation_of_duties" not in statement for statement in statements
        )
