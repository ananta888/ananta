"""Durable send reservation and receipt recovery across independent SQL engines."""

# ruff: noqa: F811 -- imported pytest fixtures are intentionally injected by name.

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session

from agent.db_models.native_dispatch import NativeDispatchDB
from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
from tests.bpmn.test_native_result_admission import native  # noqa: F401
from tests.pi.test_pi_result_completion_policy import admission, complete  # noqa: F401


def worker(task):
    return SimpleNamespace(worker_id="worker-1", worker_url=task.assigned_agent_url, revision=1)


def test_reopened_hub_does_not_resend_after_lost_transport(native):
    task = native.repository.get_by_id(native.task_id)
    journal = SQLAlchemyNativeDispatchJournal(native.engine)
    journal.begin(task=task, assignment=worker(task))
    # A different engine represents a restarted Hub, with no shared Python locks.
    reopened_engine = create_engine(native.engine.url)
    reopened = SQLAlchemyNativeDispatchJournal(reopened_engine)
    try:
        assert reopened.pending_task_ids(tenant_id=task.tenant_id) == (task.id,)
        with pytest.raises(ValueError, match="already_reserved"):
            reopened.begin(task=task, assignment=worker(task))
        with pytest.raises(ValueError, match="receipt_required"):
            reopened.reconcile(task=task)
        assert reopened.pending_task_ids(tenant_id="foreign-tenant") == ()
    finally:
        reopened_engine.dispose()


def test_recovery_closes_only_persisted_hub_receipt(native):
    task = native.repository.get_by_id(native.task_id)
    journal = SQLAlchemyNativeDispatchJournal(native.engine)
    claim = journal.begin(task=task, assignment=worker(task))
    accepted = complete(native)
    journal = SQLAlchemyNativeDispatchJournal(native.engine)
    assert journal.reconcile(task=accepted)
    assert journal.pending_task_ids(tenant_id=task.tenant_id) == ()
    journal.settle(task=task, claim=claim, accepted=False)
    with Session(native.engine) as session:
        assert session.get(NativeDispatchDB, journal._identity(task)).state == "accepted"


def test_caller_fabricated_receipt_cannot_close_unknown_send(native):
    task = native.repository.get_by_id(native.task_id)
    journal = SQLAlchemyNativeDispatchJournal(native.engine)
    claim = journal.begin(task=task, assignment=worker(task))
    task.verification_status = {"native_result_receipt": {"classification": "technical_observation"}}
    with pytest.raises(ValueError):
        journal.settle(task=task, claim=claim, accepted=True)
    assert journal.pending_task_ids(tenant_id=task.tenant_id) == (task.id,)


def test_concurrent_dispatchers_have_one_durable_send_claim(native):
    task = native.repository.get_by_id(native.task_id)
    engines = [create_engine(native.engine.url) for _ in range(2)]

    def reserve(engine):
        try:
            SQLAlchemyNativeDispatchJournal(engine).begin(task=task, assignment=worker(task))
            return "send"
        except ValueError as exc:
            return str(exc)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, engines))
        assert sorted(outcomes) == ["native_dispatch_already_reserved", "send"]
    finally:
        for engine in engines:
            engine.dispose()


def test_foreign_claim_cannot_settle_and_scan_is_bounded(native):
    task = native.repository.get_by_id(native.task_id)
    journal = SQLAlchemyNativeDispatchJournal(native.engine)
    journal.begin(task=task, assignment=worker(task))
    with pytest.raises(ValueError, match="claim_mismatch"):
        journal.settle(task=task, claim="foreign", accepted=False)
    for limit in (0, 101, True):
        with pytest.raises(ValueError, match="scan_invalid"):
            journal.pending_task_ids(tenant_id=task.tenant_id, limit=limit)


@pytest.mark.parametrize("field,value", [("revision", 2), ("worker_id", "foreign-worker")])
def test_receipt_from_different_assignment_cannot_close_dispatch(native, field, value):
    task = native.repository.get_by_id(native.task_id)
    assignment = worker(task)
    setattr(assignment, field, value)
    journal = SQLAlchemyNativeDispatchJournal(native.engine)
    journal.begin(task=task, assignment=assignment)
    accepted = complete(native)
    with pytest.raises(ValueError, match="receipt_binding_mismatch"):
        journal.reconcile(task=accepted)
    assert journal.pending_task_ids(tenant_id=task.tenant_id) == (task.id,)


def test_headless_reconciliation_is_bounded_and_preserves_unknown_task(native):
    from agent.services.native_dispatch_reconciliation import reconcile_native_dispatches

    task = native.repository.get_by_id(native.task_id)
    journal = SQLAlchemyNativeDispatchJournal(native.engine)
    journal.begin(task=task, assignment=worker(task))
    result = reconcile_native_dispatches(journal=journal, task_repository=native.repository,
                                        tenant_id=task.tenant_id, limit=1)
    assert result["scanned"] == result["unresolved"] == 1 and result["accepted"] == 0
    assert native.repository.get_by_id(task.id).status == task.status
    complete(native)
    result = reconcile_native_dispatches(journal=journal, task_repository=native.repository,
                                        tenant_id=task.tenant_id, limit=1)
    assert result["scanned"] == result["accepted"] == 1 and result["unresolved"] == 0
