"""Real scoped SQL metadata reader; no credentials or unrelated directory records."""

from dataclasses import replace

import pytest
from sqlalchemy import event
from sqlmodel import Session

from agent.db_models import OrganizationInstanceDB
from agent.models.meet_organization_topology import MeetTopologyScope
from agent.repositories.meet_role_assignment import SqlMeetRoleAssignments
from agent.services.meet_contract import MeetError
from tests.meet_dialog_lifecycle_fixture import PUBLISHER, seed_parent
from tests.test_meet_media_child_scope import organized_dialog

pytestmark = pytest.mark.timeout(45)
SCOPE = MeetTopologyScope("tenant", "project", "meet-test-org", "meet-test-unit", "meet-test-team", "meet-test-slot")


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "organization_id", "unit_id", "role_slot_id"])
def test_foreign_assignment_scope_is_not_a_directory_fallback(app, field):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        rows = SqlMeetRoleAssignments()
        assert rows.read(SCOPE, PUBLISHER).assignment_id == "meet-test-assignment"
        assert rows.read(replace(SCOPE, **{field: "foreign"}), PUBLISHER) is None
        assert rows.read(SCOPE, PUBLISHER, "another-assignment") is None
        assert rows.read(SCOPE, "http://another-publisher:8091") is None


def test_one_bounded_join_projects_no_tokens_legacy_caps_or_assignment_metadata(app):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        statements = []

        def observe(_connection, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement.lower())

        event.listen(engine, "before_cursor_execute", observe)
        try:
            assert SqlMeetRoleAssignments().read(SCOPE, PUBLISHER).digest()
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        assert len(statements) == 1 and "limit" in statements[0]
        assert all(
            value not in statements[0]
            for value in (
                "agents.token",
                "agents.capabilities",
                "assignment_metadata",
                "separation_of_duties",
                "overlays",
            )
        )


@pytest.mark.parametrize("field", ["lock_version", "definition_revision", "effective_limit_profile_hash"])
def test_changed_authoritative_organization_revision_never_rebases_existing_dialog(app, field):
    from agent.database import engine

    with app.app_context():
        f, dialog, scope, _ = organized_dialog(engine)
        with Session(engine) as session:
            row = session.get(OrganizationInstanceDB, "meet-test-org")
            setattr(row, field, row.lock_version + 1 if field == "lock_version" else "d" * 64)
            session.add(row)
            session.commit()
        with pytest.raises(MeetError, match="^meet_dialog_assignment_revoked$"):
            f.f.authority.current(dialog.id, scope.lease_id, scope.runtime_id)
