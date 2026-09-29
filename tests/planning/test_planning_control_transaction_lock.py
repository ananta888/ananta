from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent.services.planning_control_unit_of_work import planning_transaction_lock


class _RecordingSession:
    def __init__(self, dialect_name: str) -> None:
        self._bind = SimpleNamespace(
            dialect=SimpleNamespace(name=dialect_name),
        )
        self.statements: list[Any] = []

    def get_bind(self):
        return self._bind

    def exec(self, statement):
        self.statements.append(statement)


def test_planning_transaction_lock_is_a_development_backend_noop() -> None:
    session = _RecordingSession("sqlite")

    planning_transaction_lock(session, "organization:org-1:goal:goal-1")  # type: ignore[arg-type]

    assert session.statements == []


def test_planning_transaction_lock_uses_stable_namespaced_postgres_key() -> None:
    first = _RecordingSession("postgresql")
    replay = _RecordingSession("postgresql")
    other = _RecordingSession("postgresql")

    planning_transaction_lock(first, "organization:org-1:goal:goal-1")  # type: ignore[arg-type]
    planning_transaction_lock(replay, "organization:org-1:goal:goal-1")  # type: ignore[arg-type]
    planning_transaction_lock(other, "organization:org-1:goal:goal-2")  # type: ignore[arg-type]

    assert len(first.statements) == len(replay.statements) == len(other.statements) == 1
    assert str(first.statements[0]) == "SELECT pg_advisory_xact_lock(:lock_key)"
    first_key = first.statements[0].compile().params["lock_key"]
    replay_key = replay.statements[0].compile().params["lock_key"]
    other_key = other.statements[0].compile().params["lock_key"]
    assert first_key == replay_key
    assert first_key != other_key
    assert 0 <= first_key < 2**60
