"""Only monotone revocation of this browser fixture's four known SQL targets."""

import sqlite3
import time

from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from agent.db_models import OrganizationInstanceDB, OrganizationRoleAssignmentDB, OrganizationRoleSlotDB, TaskDB


def revocation_statement(reason):
    if reason == "parent-cancel":
        model, identity, value, field, old, new = (
            TaskDB,
            TaskDB.id,
            "meet-test-parent",
            TaskDB.status,
            "in_progress",
            "cancelled",
        )
    elif reason == "organization-pause":
        model, identity, value, field, old, new = (
            OrganizationInstanceDB,
            OrganizationInstanceDB.organization_id,
            "meet-test-org",
            OrganizationInstanceDB.lifecycle,
            "active",
            "paused",
        )
    elif reason == "role-draining":
        model, identity, value, field, old, new = (
            OrganizationRoleSlotDB,
            OrganizationRoleSlotDB.id,
            "meet-test-slot",
            OrganizationRoleSlotDB.lifecycle,
            "active",
            "draining",
        )
    elif reason == "assignment-suspended":
        model, identity, value, field, old, new = (
            OrganizationRoleAssignmentDB,
            OrganizationRoleAssignmentDB.id,
            "meet-test-assignment",
            OrganizationRoleAssignmentDB.lifecycle,
            "active",
            "suspended",
        )
    else:
        raise ValueError("test_lifecycle_revocation_invalid")
    return update(model).where(identity == value, field == old).values({field: new})


def revoke_fixture_lifecycle(engine, reason, *, session_factory=Session, clock=time.monotonic, pause=time.sleep):
    statement = revocation_statement(reason)
    deadline = clock() + 1
    for attempt in range(3):
        try:
            # A failed attempt rolls back and closes before any pause. No
            # grant, start, forward transition or browser assertion is retried.
            with session_factory(engine) as session:
                result = session.execute(statement)
                if result.rowcount != 1:
                    raise ValueError("test_lifecycle_revocation_target_changed")
                session.commit()
            return attempt + 1
        except OperationalError as error:
            code = getattr(error.orig, "sqlite_errorcode", 0)
            if (
                not isinstance(error.orig, sqlite3.OperationalError)
                or type(code) is not int
                or code & 255 not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                or attempt == 2
                or clock() + 0.05 >= deadline
            ):
                raise
            pause(0.05)
