from __future__ import annotations

import pytest

from agent.repositories.organizations.goals import SqlOrganizationGoalRepository
from agent.services.organization_unit_of_work import OrganizationUnitOfWork


class RecordingSession:
    def __init__(self, *, fail_commit: bool = False) -> None:
        self.fail_commit = fail_commit
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.flushes = 0

    def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("commit_failed")

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1

    def flush(self) -> None:
        self.flushes += 1


def test_uow_commits_and_closes_exactly_once() -> None:
    session = RecordingSession()

    with OrganizationUnitOfWork(session_factory=lambda: session) as uow:
        assert isinstance(uow.goals, SqlOrganizationGoalRepository)
        uow.flush()

    assert (session.commits, session.rollbacks, session.closes, session.flushes) == (1, 0, 1, 1)


def test_uow_rolls_back_domain_failure_exactly_once() -> None:
    session = RecordingSession()

    with pytest.raises(RuntimeError, match="domain_failed"):
        with OrganizationUnitOfWork(session_factory=lambda: session):
            raise RuntimeError("domain_failed")

    assert (session.commits, session.rollbacks, session.closes) == (0, 1, 1)


def test_uow_rolls_back_failed_commit_and_does_not_retry() -> None:
    session = RecordingSession(fail_commit=True)

    with pytest.raises(RuntimeError, match="commit_failed"):
        with OrganizationUnitOfWork(session_factory=lambda: session):
            pass

    assert (session.commits, session.rollbacks, session.closes) == (1, 1, 1)
