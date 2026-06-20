"""Reproducer baseline for STAB-OPEN-1 (cross-file test-order flake).

BEFORE the DI-Layer refactor: in the full pytest run,
``test_result_memory_handles_missing_optional_fields_without_silent_inconsistency``
in ``test_result_memory_service.py`` may go RED with one of two symptoms:

  Symptom A (more common):
    entry_id = <MagicMock name='mock.save().id' ...>
    sqlalchemy.exc.InvalidRequestError: ... 'memory_entries.id'

  Symptom B (also seen):
    sqlalchemy.exc.InvalidRequestError:
      Incorrect number of values in identifier to formulate
      primary key for session.get()

Both are caused by chained monkeypatch on a shared singleton repo object:
``test_awf_worker_fixup_t021_t030.py::TestT022MemoryPolicy::test_enabled_false_skips_write``
patches ``agent.repository.memory_entry_repo.save`` on the shared object,
and ``test_result_memory_and_federation.py`` patches
``agent.services.result_memory_service.memory_entry_repo`` as a module
symbol. The patches leak across file boundaries in the full run.

This scratch test reproduces the relevant cross-file pattern as two
separate pytest tests, run in the order that triggers the flake:

1. ``test_awf_style_patches_di_layer`` — runs the awf-style monkeypatch
   pattern (now targeting ``agent.services.di.memory_entry_repo`` after
   the DI-Layer refactor) and verifies the patched fake is the
   service's resolved repo.
2. ``test_optional_fields_passes_after_awf`` — runs the optional-fields
   test, which previously failed because the leaked ``.save`` mutation
   from test 1 caused ``entry.id`` to be a Mock. After the DI-Layer
   refactor, this must pass.

TDD discipline: this is a baseline measurement, NOT a fix. Do not
silence the failure when the refactor is incomplete.
"""
from __future__ import annotations


class _FakeMemoryRepo:
    """Minimal stub of MemoryEntryRepository for the result_memory service."""

    def __init__(self) -> None:
        self.saved: list = []
        self._next_id = 0

    def save(self, entry):
        self._next_id += 1
        try:
            entry.id = f"fake-{self._next_id}"
        except Exception:
            pass
        self.saved.append(entry)
        return entry

    def get_by_id(self, entry_id):
        for entry in self.saved:
            if getattr(entry, "id", None) == entry_id:
                return entry
        return None


def test_awf_style_patches_di_layer(db_session, monkeypatch):
    """Replicates the body of
    ``tests/test_awf_worker_fixup_t021_t030.py::TestT022MemoryPolicy::test_enabled_false_skips_write``.

    POST-DI-LAYER: patches ``agent.services.di.memory_entry_repo`` (the
    whole object) rather than mutating ``.save`` on the singleton. This
    is the test pattern that the DI-Layer refactor enables: replacing
    the whole repository object via the factory's lookup root instead
    of mutating a method on the shared singleton (Pitfall 11).
    """
    from agent.services.result_memory_service import ResultMemoryService

    fake = _FakeMemoryRepo()
    monkeypatch.setattr("agent.services.di.memory_entry_repo", fake)

    svc = ResultMemoryService()
    # Sanity: the service must resolve to our fake via the property.
    assert svc._memory_entry_repo is fake, (
        f"DI-layer not wired: service resolved to {svc._memory_entry_repo!r}"
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
    assert len(fake.saved) == 0


def test_optional_fields_passes_after_awf(db_session, monkeypatch):
    """Replicates the body of
    ``tests/test_result_memory_service.py::test_result_memory_handles_missing_optional_fields_without_silent_inconsistency``.

    Pre-DI-LAYER: this test fails in the full run because
    ``test_awf_style_patches_di_layer`` (in the awf file) leaks a
    ``.save`` mutation on the shared singleton repo. The optional-fields
    test then receives a Mock-typed ``entry.id`` and SQLAlchemy raises.

    POST-DI-LAYER: the property-based repo lookup in
    ``ResultMemoryService`` resolves the *current* value of
    ``agent.services.di.memory_entry_repo`` on every call. We patch a
    fresh fake here (in this test's monkeypatch scope), so this test
    sees only its own fake. The awf test's fake was scoped to its own
    monkeypatch and was torn down at the end of that test.
    """
    fake = _FakeMemoryRepo()
    monkeypatch.setattr("agent.services.di.memory_entry_repo", fake)

    # Call the service. It will use the fake via the property lookup.
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
    # Round-trip via the fake's get_by_id.
    saved = fake.get_by_id(entry.id)
    assert saved is not None, (
        f"STAB-OPEN-1 still active: fake.get_by_id({entry.id!r}) returned None"
    )
