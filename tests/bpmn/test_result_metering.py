"""Failed executions consume the same Hub-owned run budget as successes."""

from dataclasses import replace

import pytest

from agent.services.bpmn_run_lease import BpmnRunLeaseService
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.persistence import SQLiteCheckpointStore
from tests.bpmn.completion_helpers import harness as harness
from tests.bpmn.completion_helpers import linear_request


def test_failed_worker_cannot_avoid_run_budget_or_trigger_a_retry(harness):
    request = linear_request()
    request = replace(
        request, plan=replace(request.plan, budget=replace(request.plan.budget, max_tokens=1, max_attempts=3))
    )
    harness.dispatch_first(request)
    first = harness.queue.results["hub-task-1"]
    harness.queue.results["hub-task-1"] = replace(
        first, status="failed", reason_code="synthetic_failure", budget_usage={"tokens": 2}
    )
    result = harness.hub.advance(request)
    assert result.status == "failed"
    assert result.reason_code == "native_budget_exceeded:tokens"
    assert harness.handler.calls == ["work"]
    harness.restart()
    assert harness.hub.advance(request).status == "failed"


def test_terminal_worker_failure_retains_valid_consumption(harness):
    request = linear_request()
    harness.dispatch_first(request)
    first = harness.queue.results["hub-task-1"]
    harness.queue.results["hub-task-1"] = replace(
        first, status="failed", reason_code="synthetic_failure", budget_usage={"tokens": 2, "cost_micros": 3}
    )
    result = harness.hub.advance(request)
    assert result.status == "failed"
    assert result.checkpoint.state.runtime_metadata["budget_usage"] == {"tokens": 2, "cost_micros": 3}
    assert harness.handler.calls == ["work"]


@pytest.mark.parametrize("status,method", [("completed", "acknowledge_result"), ("failed", "fail_attempt")])
def test_result_recipient_rechecks_lease_inside_the_ownership_transaction(harness, monkeypatch, status, method):
    request = linear_request()
    previous = harness.dispatch_first(request)
    first = harness.queue.results["hub-task-1"]
    harness.queue.results["hub-task-1"] = replace(first, status=status)
    store = harness.stores["ownership"]
    original = getattr(store, method)
    takeover_store = SQLiteCheckpointStore(harness.directory / "checkpoints.sqlite")
    harness.connections.append(takeover_store)
    takeover = BpmnRunLeaseService(checkpoints=takeover_store, keys=harness.keys, clock=lambda: harness.now)

    def delayed(**values):
        harness.now = 131.0
        with takeover.acquire(request):
            return original(**values)

    monkeypatch.setattr(store, method, delayed)
    with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
        harness.hub.advance(request)
    owner = store.get(tenant_id=request.plan.tenant_id, run_id=request.run_id, step_id="work")
    assert owner.status == "active"
    assert harness.hub.checkpoint(request) == previous.checkpoint
    assert harness.handler.calls == ["work"]
