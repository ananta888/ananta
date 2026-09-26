"""Headless operation fencing using real signed checkpoint/CAS implementations."""

from dataclasses import replace

import pytest

from agent.services.bpmn_run_lease import BpmnRunLeaseService
from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_runtime import HmacKeyRing, InMemoryCheckpointStore
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from tests.bpmn.test_execution_routing import compile_request, execution_plan, xor_xml


def setup():
    clock = [100.0]
    store = InMemoryCheckpointStore()
    keys = HmacKeyRing({"test": "k" * 32}, active_key_id="test")
    service = BpmnRunLeaseService(checkpoints=store, keys=keys, clock=lambda: clock[0])
    request = NativeGraphRequest(execution_plan(compile_request(xor_xml())), "synthetic-lease", "control")
    return clock, service, request


def test_competing_hub_call_is_rejected_and_next_call_gets_new_fence():
    _, service, request = setup()
    with service.acquire(request) as first:
        first.ensure_valid()
        with pytest.raises(OptimisticConcurrencyError, match="lease_held"):
            with service.acquire(request):
                pytest.fail("Concurrent control owner was admitted")
    with service.acquire(request) as second:
        assert second.fencing_token > first.fencing_token
        with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
            first.ensure_valid()


def test_expired_writer_cannot_release_or_use_successor_lease():
    clock, service, request = setup()
    first_scope = service.acquire(request)
    first = first_scope.__enter__()
    clock[0] = 131.0
    with service.acquire(request) as second:
        first_scope.__exit__(None, None, None)
        second.ensure_valid()
        with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
            first.ensure_valid()


def test_lease_release_on_failure_and_control_binding():
    _, service, request = setup()
    with pytest.raises(RuntimeError, match="fault"):
        with service.acquire(request):
            raise RuntimeError("fault")
    with service.acquire(request):
        pass
    with pytest.raises(OptimisticConcurrencyError, match="control_task_binding_mismatch"):
        with service.acquire(replace(request, control_task_id="foreign")):
            pytest.fail("Control binding drift accepted")
