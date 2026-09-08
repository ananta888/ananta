"""Two explicit role slots in one test-owned Organization; no user directory writes."""

from sqlmodel import Session

from agent.db_models import AgentInfoDB, OrganizationRoleAssignmentDB, OrganizationRoleSlotDB, TaskDB
from tests.meet_dialog_lifecycle_fixture import PUBLISHER, seed_parent

SECOND_PUBLISHER = "http://synthetic-second-publisher:8091"
PARENTS = ("meet-test-parent", "meet-test-parent-second")


def seed_multi_role_parents(engine, *, publishers=(PUBLISHER, SECOND_PUBLISHER), tenant="tenant", project="project"):
    seed_parent(engine, tenant=tenant, project=project, publisher=publishers[0])
    scope = {"tenant_id": tenant, "project_id": project, "organization_id": "meet-test-org"}
    with Session(engine) as session:
        policy = session.get(OrganizationRoleSlotDB, "meet-test-slot").assignment_policy
        session.add(
            OrganizationRoleSlotDB(
                **scope,
                id="meet-test-slot-second",
                unit_id="meet-test-unit",
                slot_key="synthetic-second",
                role_template_key="synthetic",
                role_template_version=1,
                assignment_policy=policy,
            )
        )
        session.add(
            AgentInfoDB(
                url=publishers[1],
                name="Synthetic second Meet publisher",
                registration_validated=True,
                registration_provenance="synthetic-test",
                authorized_capabilities=["meet_dialog_session"],
            )
        )
        session.commit()
        session.add(
            OrganizationRoleAssignmentDB(
                **scope,
                id="meet-test-assignment-second",
                role_slot_id="meet-test-slot-second",
                agent_url=publishers[1],
                lifecycle="active",
                assigned_at=1000.0,
            )
        )
        session.add(
            TaskDB(
                **scope,
                id=PARENTS[1],
                unit_id="meet-test-unit",
                team_id="meet-test-team",
                role_slot_id="meet-test-slot-second",
                status="in_progress",
                title="Synthetic second parent",
            )
        )
        session.commit()
