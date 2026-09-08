"""Synthetic organization graph; actual SQL topology must remain current."""

import pytest
from sqlmodel import Session

from agent.db_models import OrganizationRoleSlotDB, OrganizationUnitDB, TeamDB
from agent.services.meet_contract import MeetError
from tests.test_meet_media_child_scope import organized_dialog

pytestmark = pytest.mark.timeout(45)


@pytest.mark.parametrize("leaf", ["unit", "team", "role"])
def test_active_organization_does_not_authorize_deactivated_topology(app, leaf):
    from agent.database import engine

    with app.app_context():
        f, dialog, scope, _ = organized_dialog(engine)
        with Session(engine) as session:
            if leaf == "unit":
                row = session.get(OrganizationUnitDB, "meet-test-unit")
                row.lifecycle = "draining"
            elif leaf == "role":
                row = session.get(OrganizationRoleSlotDB, "meet-test-slot")
                row.lifecycle = "draining"
            else:
                row = session.get(TeamDB, "meet-test-team")
                row.is_active = False
            session.add(row)
            session.commit()
        f.f.binding.read.reset_mock()
        with pytest.raises(MeetError, match="^meet_dialog_organization_inactive$"):
            f.f.authority.current(dialog.id, scope.lease_id, scope.runtime_id)
        f.f.binding.read.assert_not_called()
