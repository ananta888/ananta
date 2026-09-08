"""Known SQL contention is bounded; permissions and success checks are never retried."""

import sqlite3
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from agent.db_models import OrganizationInstanceDB, OrganizationRoleAssignmentDB, OrganizationRoleSlotDB, TaskDB
from tests.meet_dialog_lifecycle_fixture import seed_parent
from tests.meet_lifecycle_revocation import revoke_fixture_lifecycle
from tests.test_meet_dialog_cleanup import locked

pytestmark = pytest.mark.timeout(45)


def factory_with(*errors):
    factory = Mock()
    sessions = []
    for error in errors:
        session = MagicMock()
        session.__enter__.return_value = session
        if error is not None:
            session.execute.side_effect = error
        else:
            session.execute.return_value.rowcount = 1
        sessions.append(session)
    factory.side_effect = sessions
    return factory, sessions


def test_locked_attempts_close_before_pause_and_never_reuse_failed_transactions():
    factory, sessions = factory_with(locked(), locked(), None)
    pauses = []

    def pause(delay):
        index = len(pauses)
        sessions[index].__exit__.assert_called_once()
        sessions[index].commit.assert_not_called()
        pauses.append(delay)

    assert (
        revoke_fixture_lifecycle("engine", "role-draining", session_factory=factory, clock=lambda: 0, pause=pause) == 3
    )
    assert pauses == [0.05, 0.05] and factory.call_count == 3
    sessions[-1].commit.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        ValueError("policy_denied"),
        OperationalError("sql", {}, Exception("not sqlite")),
        OperationalError("sql", {}, sqlite3.OperationalError("not a lock")),
    ],
)
def test_no_other_error_or_unknown_target_is_retried(error):
    factory, _ = factory_with(error)
    pause = Mock()
    with pytest.raises(type(error)):
        revoke_fixture_lifecycle("engine", "role-draining", session_factory=factory, pause=pause)
    assert factory.call_count == 1
    pause.assert_not_called()
    factory.reset_mock()
    with pytest.raises(ValueError, match="^test_lifecycle_revocation_invalid$"):
        revoke_fixture_lifecycle("engine", "activate-role", session_factory=factory)
    factory.assert_not_called()


def test_attempt_exhaustion_and_retry_deadline_remain_failures():
    factory, _ = factory_with(locked(), locked(), locked())
    with pytest.raises(OperationalError):
        revoke_fixture_lifecycle("engine", "role-draining", session_factory=factory, clock=lambda: 0, pause=Mock())
    assert factory.call_count == 3
    factory, _ = factory_with(locked())
    pause = Mock()
    with pytest.raises(OperationalError):
        revoke_fixture_lifecycle(
            "engine", "role-draining", session_factory=factory, clock=Mock(side_effect=[0, 2]), pause=pause
        )
    pause.assert_not_called()


@pytest.mark.parametrize("reason", ["parent-cancel", "organization-pause", "role-draining", "assignment-suspended"])
def test_exact_real_targets_are_monotone_and_second_application_is_not_success(app, reason):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        assert revoke_fixture_lifecycle(engine, reason) == 1
        expected = {
            "parent-cancel": "in_progress",
            "organization-pause": "active",
            "role-draining": "active",
            "assignment-suspended": "active",
        }
        expected[reason] = {
            "parent-cancel": "cancelled",
            "organization-pause": "paused",
            "role-draining": "draining",
            "assignment-suspended": "suspended",
        }[reason]
        with Session(engine) as session:
            assert {
                "parent-cancel": session.get(TaskDB, "meet-test-parent").status,
                "organization-pause": session.get(OrganizationInstanceDB, "meet-test-org").lifecycle,
                "role-draining": session.get(OrganizationRoleSlotDB, "meet-test-slot").lifecycle,
                "assignment-suspended": session.get(OrganizationRoleAssignmentDB, "meet-test-assignment").lifecycle,
            } == expected
        with pytest.raises(ValueError, match="^test_lifecycle_revocation_target_changed$"):
            revoke_fixture_lifecycle(engine, reason)


def test_actual_shared_cache_read_lock_is_released_before_revocation_retry(app):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        reader = engine.raw_connection()
        try:
            cursor = reader.cursor()
            cursor.execute("BEGIN")
            cursor.execute("SELECT lifecycle FROM organization_role_slots WHERE id='meet-test-slot'")
            assert cursor.fetchone() == ("active",)
            pauses = []

            def release(delay):
                pauses.append(delay)
                reader.rollback()

            assert revoke_fixture_lifecycle(engine, "role-draining", pause=release) == 2
            assert pauses == [0.05]
            with Session(engine) as session:
                assert session.get(OrganizationRoleSlotDB, "meet-test-slot").lifecycle == "draining"
        finally:
            reader.rollback()
            reader.close()
