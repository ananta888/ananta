"""Task cancellation persists history/status under the same signed lease guard."""

from types import SimpleNamespace

import pytest

from agent.services.native_result_completion_policy import NativeResultCompletionPolicy
from agent.services.native_task_cancellation import cancel_native_task_fenced
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from tests.bpmn.test_fenced_task_ingestion import setup as task_scope  # noqa: F401 -- pytest fixture


@pytest.fixture
def cancellation(task_scope):  # noqa: F811 -- imported pytest fixture
    def unexpected_result_authority(**kwargs):
        pytest.fail("Hub cancellation must not claim a Worker result")

    task_scope.repository._completion_policy = NativeResultCompletionPolicy(
        authority=SimpleNamespace(require_current=unexpected_result_authority),
    )
    task_scope.cancelled = []
    task_scope.runtime = SimpleNamespace(
        notify_native_task_cancelled=lambda task, **kwargs: task_scope.cancelled.append((task.id, kwargs)),
    )
    return task_scope


def cancel(s, task_id, lease, *, tenant_id=None):
    return cancel_native_task_fenced(
        repository=s.repository, runtime=s.runtime, task_id=task_id, lease=lease,
        tenant_id=tenant_id or s.request.plan.tenant_id, run_id=s.request.run_id, reason="synthetic-cancel",
    )


def test_cancel_commits_status_history_and_notifies_once(cancellation):
    s = cancellation
    with s.leases.acquire(s.request) as lease:
        receipt = s.adapter.submit_fenced(s.command, lease=lease)
        cancel(s, receipt.hub_task_id, lease)
        cancel(s, receipt.hub_task_id, lease)
    task = s.repository.get_by_id(receipt.hub_task_id)
    assert task.status == "cancelled" and len(task.history) == 2
    assert task.history[-1]["event_type"] == "workflow_node_task_cancelled"
    assert len(s.cancelled) == 1 and not task.verification_status


@pytest.mark.parametrize("status", ["waiting_for_review", "delegated", "blocked_by_dependency", "reserved"])
def test_signed_cancel_handles_all_active_dispatch_states(cancellation, status):
    from sqlmodel import Session

    from agent.db_models import TaskDB

    s = cancellation
    with s.leases.acquire(s.request) as lease:
        receipt = s.adapter.submit_fenced(s.command, lease=lease)
        with Session(s.engine) as session:
            task = session.get(TaskDB, receipt.hub_task_id)
            task.status = status
            session.add(task)
            session.commit()
        cancel(s, receipt.hub_task_id, lease)
    assert s.repository.get_by_id(receipt.hub_task_id).status == "cancelled"
    assert len(s.cancelled) == 1


def test_expiry_inside_status_recipient_does_not_cancel_or_notify(cancellation, monkeypatch):
    from agent.services import native_task_cancellation

    s = cancellation
    validate = native_task_cancellation.validate_sqlalchemy_lease

    def expire_inside_recipient(*args, **kwargs):
        s.now[0] = 131.0
        return validate(*args, **kwargs)

    with s.leases.acquire(s.request) as lease:
        receipt = s.adapter.submit_fenced(s.command, lease=lease)
        before = s.repository.get_by_id(receipt.hub_task_id).model_dump()
        monkeypatch.setattr(native_task_cancellation, "validate_sqlalchemy_lease", expire_inside_recipient)
        with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
            cancel(s, receipt.hub_task_id, lease)
    assert s.repository.get_by_id(receipt.hub_task_id).model_dump() == before
    assert s.cancelled == []


def test_foreign_run_binding_never_cancels_the_task(cancellation):
    s = cancellation
    with s.leases.acquire(s.request) as lease:
        receipt = s.adapter.submit_fenced(s.command, lease=lease)
        with pytest.raises(ValueError, match="binding_mismatch"):
            cancel(s, receipt.hub_task_id, lease, tenant_id="foreign")
    assert s.repository.get_by_id(receipt.hub_task_id).status == "created" and s.cancelled == []


def test_idempotent_terminal_cancel_still_checks_authoritative_scope(cancellation):
    s = cancellation
    with s.leases.acquire(s.request) as lease:
        receipt = s.adapter.submit_fenced(s.command, lease=lease)
        cancel(s, receipt.hub_task_id, lease)
        with pytest.raises(ValueError, match="binding_mismatch"):
            cancel(s, receipt.hub_task_id, lease, tenant_id="foreign")
    assert len(s.cancelled) == 1


def test_superseded_control_owner_cannot_cancel(cancellation):
    s = cancellation
    with s.leases.acquire(s.request) as stale:
        receipt = s.adapter.submit_fenced(s.command, lease=stale)
        s.now[0] = 131.0
        with s.leases.acquire(s.request) as current:
            with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
                cancel(s, receipt.hub_task_id, stale)
            cancel(s, receipt.hub_task_id, current)
    assert s.repository.get_by_id(receipt.hub_task_id).status == "cancelled" and len(s.cancelled) == 1
