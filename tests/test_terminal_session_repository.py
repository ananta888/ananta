from __future__ import annotations

import time

import pytest

from agent.db_models import TerminalEventDB, TerminalSessionDB
from agent.repository import terminal_event_repo, terminal_session_repo


def _session(status: str = "created") -> TerminalSessionDB:
    now = time.time()
    return TerminalSessionDB(
        created_at=now,
        updated_at=now,
        created_by_user_id="u1",
        created_by_username="user1",
        target_type="worker",
        target_id="w1",
        policy_decision_id="dec-1",
        status=status,
    )


def test_terminal_session_repository_requires_target_user_and_policy_decision():
    with pytest.raises(ValueError, match="terminal_session_missing_target_type"):
        terminal_session_repo.save(
            TerminalSessionDB(
                created_by_user_id="u1",
                created_by_username="user1",
                target_type="",
                target_id="w1",
                policy_decision_id="dec-1",
            )
        )

    with pytest.raises(ValueError, match="terminal_session_missing_policy_decision_id"):
        terminal_session_repo.save(
            TerminalSessionDB(
                created_by_user_id="u1",
                created_by_username="user1",
                target_type="worker",
                target_id="w1",
                policy_decision_id="",
            )
        )


def test_terminal_session_status_transitions_are_persisted():
    saved = terminal_session_repo.save(_session("created"))
    running = terminal_session_repo.transition_status(saved.id, "running")
    attached = terminal_session_repo.transition_status(saved.id, "attached")
    detached = terminal_session_repo.transition_status(saved.id, "detached")
    killed = terminal_session_repo.transition_status(saved.id, "killed")

    assert running is not None and running.status == "running"
    assert attached is not None and attached.status == "attached"
    assert detached is not None and detached.status == "detached"
    assert killed is not None and killed.status == "killed"


def test_terminal_event_repository_is_append_only():
    session = terminal_session_repo.save(_session("running"))
    first = terminal_event_repo.append(
        TerminalEventDB(
            session_id=session.id,
            user_id="u1",
            event_type="session_created",
            target_type="worker",
            target_id="w1",
            operation="create",
            allowed=True,
            reason_code="ok",
            summary="created",
        )
    )
    second = terminal_event_repo.append(
        TerminalEventDB(
            session_id=session.id,
            user_id="u1",
            event_type="session_attached",
            target_type="worker",
            target_id="w1",
            operation="attach",
            allowed=True,
            reason_code="ok",
            summary="attached",
        )
    )

    events = terminal_event_repo.list_by_session(session.id)
    assert [item.id for item in events] == [first.id, second.id]
