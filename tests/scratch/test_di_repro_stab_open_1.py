"""Reproducer baseline for STAB-OPEN-1 (cross-file test-order flake).

BEFORE the DI-Layer refactor: in the full pytest run, this test may go RED
with one of two symptoms:

  Symptom A (more common):
    entry_id = <MagicMock name='mock.save().id' ...>
    sqlalchemy.exc.InvalidRequestError: ... 'memory_entries.id'

  Symptom B (also seen):
    sqlalchemy.exc.InvalidRequestError:
      Incorrect number of values in identifier to formulate
      primary key for session.get()

Both are caused by chained monkeypatch on a shared singleton repo object:
`test_awf_worker_fixup_t021_t030.py` patches
`agent.repository.memory_entry_repo.save` on the shared object, and
`test_result_memory_and_federation.py` patches
`agent.services.result_memory_service.memory_entry_repo` as a module symbol.
The patches leak across file boundaries in the full run.

This scratch test reproduces the relevant cross-file pattern in a single
session: the awf-style test runs first, then the result_memory test runs in
the same Python process. Both must pass.

After the DI-Layer refactor (commit chain W2), this test stays green in any
run order.

TDD: this is a baseline measurement, NOT a fix. Do not silence the failure
when the refactor is incomplete.
"""
from __future__ import annotations

import pytest


def _run_awf_style_test(monkeypatch):
    """Replicates the relevant body of
    tests/test_awf_worker_fixup_t021_t030.py::TestResultMemoryServiceT021T030::test_enabled_false_skips_write
    without needing the AWF harness fixture (tmp_path).
    """
    from agent.services.result_memory_service import ResultMemoryService

    svc = ResultMemoryService()
    saved: list = []
    # PITFALL 11 — chained monkeypatch on a shared singleton repo object
    monkeypatch.setattr(
        "agent.repository.memory_entry_repo.save",
        lambda e: saved.append(e) or e,
    )
    result = svc.record_worker_result_memory(
        task_id="t-1",
        goal_id=None,
        trace_id=None,
        worker_job_id=None,
        title="t",
        output="some output",
        policy={"enabled": False},
    )
    assert result is None
    assert len(saved) == 0
    return True


def _run_optional_fields_test():
    """Replicates the relevant body of
    tests/test_result_memory_service.py::test_result_memory_handles_missing_optional_fields_without_silent_inconsistency
    """
    from agent.repository import memory_entry_repo
    from agent.services.result_memory_service import ResultMemoryService

    entry = ResultMemoryService().record_worker_result_memory(
        task_id=None,
        goal_id=None,
        trace_id=None,
        worker_job_id=None,
        title=None,
        output="",
        artifact_refs=None,
        retrieval_tags=None,
        metadata=None,
        policy={"create_followup_artifact": False},
    )
    assert entry is not None
    saved = memory_entry_repo.get_by_id(entry.id)
    assert saved is not None
    assert saved.task_id is None
    assert saved.summary is None
    assert saved.artifact_refs == []
    assert saved.retrieval_tags == []
    assert (saved.memory_metadata or {}).get("followup_artifact") is None
    assert (saved.memory_metadata or {}).get("memory_format") == "worker_result_compact_v3"
    return True


def test_pair_awf_and_result_memory_optional_fields_runs_clean(
    tmp_path, monkeypatch, db_session
):
    """Both tests run in the same session. Pre-DI-layer this can leak the
    monkeypatched `memory_entry_repo.save` lambda into the second test
    (returning a Mock that the second test then calls .id on)."""
    # Order matters: this is the order that triggers the flake in the full run.
    _run_awf_style_test(monkeypatch)
    _run_optional_fields_test()


def test_optional_fields_alone_runs_clean(db_session):
    """Sanity: the optional-fields test passes in isolation (it always did)."""
    _run_optional_fields_test()


def test_awf_style_alone_runs_clean(tmp_path, monkeypatch, db_session):
    """Sanity: the awf-style test passes in isolation (it always did)."""
    _run_awf_style_test(monkeypatch)
