import pytest
from sqlalchemy.exc import ProgrammingError

from agent.repositories.memory import MemoryEntryRepository
import agent.repositories.memory as memory_module


class _MissingTableSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def exec(self, _statement):
        raise ProgrammingError(
            "SELECT * FROM memory_entries",
            {},
            Exception('relation "memory_entries" does not exist'),
        )


class _UnexpectedProgrammingErrorSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def exec(self, _statement):
        raise ProgrammingError(
            "SELECT * FROM memory_entries",
            {},
            Exception("syntax error at or near SELECT"),
        )


def test_get_by_goal_returns_empty_when_memory_entries_table_missing(monkeypatch):
    monkeypatch.setattr(memory_module, "Session", lambda _engine: _MissingTableSession())
    repo = MemoryEntryRepository()

    assert repo.get_by_goal("goal-1") == []


def test_get_by_goal_reraises_unexpected_programming_error(monkeypatch):
    monkeypatch.setattr(memory_module, "Session", lambda _engine: _UnexpectedProgrammingErrorSession())
    repo = MemoryEntryRepository()

    with pytest.raises(ProgrammingError):
        repo.get_by_goal("goal-1")
