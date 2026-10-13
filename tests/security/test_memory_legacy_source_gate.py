"""AST guard regression tests, no application imports or production evidence."""

import pytest

from scripts.check_memory_legacy_access import legacy_access_violations


@pytest.mark.parametrize("source", [
    "from agent.repository import memory_entry_repo as memory\nmemory.get_by_task('x')",
    "from agent.repository import memory_entry_repo as memory\ncopy=memory\ncopy.get_by_goal('x')",
    "from agent.repositories.memory import MemoryEntryRepository as Repo\nr=Repo()\nr.save('x')",
    "repos.memory_entry_repo.get_by_namespace('x')",
    "from agent.repository import memory_entry_repo\nr = injected or memory_entry_repo\nr.get_by_id('x')",
    "r=get_memory_entry_repository()\nr.get_by_task('x')",
    "from agent.repository import memory_entry_repo\nr: object = memory_entry_repo\nr.get_by_task('x')",
    "import agent.repositories.memory as storage\nr=storage.MemoryEntryRepository()\nr.save('x')",
    "from agent.repositories import memory as storage\nr=storage.MemoryEntryRepository()\nr.get_by_id('x')",
    "import agent.repositories.memory\nr=agent.repositories.memory.MemoryEntryRepository()\nr.get_by_task('x')",
    "from agent.repository import memory_entry_repo\nread = memory_entry_repo.get_by_task\nread('x')",
])
def test_raw_port_and_aliases_fail(source):
    assert legacy_access_violations("agent/services/unsafe.py", source)


def test_scoped_reader_and_unrelated_task_repository_are_allowed():
    source = """
from agent.repository import memory_entry_repo
reader = scoped_memory_reader(memory_entry_repo)
reader.get_by_task('x')
typed_reader: object = scoped_memory_reader(memory_entry_repo)
typed_reader.get_by_task('x')
tasks.get_by_task('x')
import unrelated as storage
other = storage.MemoryEntryRepository()
other.get_by_task('x')
"""
    assert legacy_access_violations("agent/services/safe.py", source) == []


def test_source_is_never_executed_and_raw_port_allowlist_is_exact():
    assert legacy_access_violations("agent/services/unknown.py", "raise RuntimeError('must not execute')") == []
    source = "repos.memory_entry_repo.get_by_task('x')"
    assert legacy_access_violations("agent/services/result_memory_service.py", source) == []
    assert legacy_access_violations("agent/services/result_memory_service_copy.py", source)
