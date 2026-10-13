"""Actual PostgreSQL event-wait CAS and recipient fences with signed Hub leases."""

# ruff: noqa: F811 -- imported pytest fixture is deliberately injected by name.

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from agent.services.bpmn_event_wait import BpmnEventWaitService
from agent.services.bpmn_event_wait_contracts import TimerSpec, WaitRunBinding
from agent.services.bpmn_event_wait_store import CheckpointEventWaitStore
from agent.services.bpmn_run_lease import BpmnRunLeaseService
from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.security import HmacKeyRing
from agent.services.workflow_runtime.sqlalchemy_event_stores import SQLAlchemyCheckpointStore
from tests.bpmn.test_event_wait import CONTRACT, TARGET, RacingStore, ValidateOrder, message
from tests.bpmn.test_execution_routing import compile_request, execution_plan, xor_xml
from tests.bpmn.test_native_postgres_concurrency import engines
from tests.bpmn.test_native_postgres_concurrency import pg_native as pg_native


def wait_setup(native):
    connections = engines(native)
    plan = execution_plan(compile_request(xor_xml()))
    request = NativeGraphRequest(plan, 'postgres-event-waits', 'synthetic-control-task')
    run = WaitRunBinding(plan.tenant_id, 'synthetic-project', plan.workflow_id,
        request.run_id, 'synthetic-definition-v1', plan.plan_hash, plan.policy_version)
    keys = HmacKeyRing({'synthetic': b'synthetic-event-wait-signing-key-only'}, active_key_id='synthetic')
    now = [100.0]
    checkpoints = [SQLAlchemyCheckpointStore(engine) for engine in connections]
    leases = [BpmnRunLeaseService(checkpoints=store, keys=keys, clock=lambda: now[0]) for store in checkpoints]

    class AdmitExactMessage:
        def authorize(self, item):
            if item.run != run or item.target != TARGET or item.contract != CONTRACT:
                raise PermissionError('synthetic_message_scope_denied')

    def service(index, lease):
        store = CheckpointEventWaitStore(checkpoints[index], keys).for_lease(lease)
        return BpmnEventWaitService(run=run, store=store, clock=lambda: now[0],
            fencing_token=lease.fencing_token, guard=lease.ensure_valid,
            authorizer=AdmitExactMessage(), payload_validator=ValidateOrder())

    return connections, run, now, leases, request, service


def racing(a, b):
    barrier = Barrier(2, timeout=5)
    for service in (a, b):
        service._store = RacingStore(service._store, barrier)
    return a, b


def test_postgres_timer_cancel_consume_has_one_durable_winner(pg_native):
    connections, _, _, leases, request, service = wait_setup(pg_native)
    try:
        with leases[0].acquire(request) as lease:
            view = service(0, lease).arm_timer(TARGET, TimerSpec(duration_seconds=0), timeout_seconds=50)
            consumer, canceller = racing(service(0, lease), service(1, lease))
            with ThreadPoolExecutor(max_workers=2) as pool:
                consumed = pool.submit(consumer.consume, TARGET, wakeup_id=view.wakeup_id)
                cancelled = pool.submit(canceller.cancel, TARGET)
                receipt, cancellation = consumed.result(timeout=5), cancelled.result(timeout=5)
            final = service(1, lease).inspect(TARGET)
            if final.status == 'cancelled':
                assert receipt is None and cancellation.status == 'cancelled'
            else:
                assert final.status == cancellation.status == 'consumed' and receipt is not None
            assert service(1, lease).pending() == ()
    finally:
        for engine in connections:
            engine.dispose()


def test_postgres_duplicate_message_and_consume_share_persisted_receipt(pg_native):
    connections, run, _, leases, request, service = wait_setup(pg_native)
    try:
        with leases[0].acquire(request) as lease:
            service(0, lease).subscribe_message(TARGET, CONTRACT, timeout_seconds=50)
            item = replace(message(), run=run)
            a, b = racing(service(0, lease), service(1, lease))
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(hub.deliver_message, item) for hub in (a, b)]
                assert [result.result(timeout=5).status for result in results] == ['matched', 'matched']
            ready, = service(0, lease).pending()
            a, b = racing(service(0, lease), service(1, lease))
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(hub.consume, TARGET, wakeup_id=ready.wakeup_id) for hub in (a, b)]
                receipt = results[0].result(timeout=5)
                assert receipt is not None and receipt == results[1].result(timeout=5)
            assert service(1, lease).pending() == ()
        with leases[1].acquire(request) as successor:
            assert service(1, successor).consume(TARGET, wakeup_id=ready.wakeup_id) == receipt
    finally:
        for engine in connections:
            engine.dispose()


def test_postgres_successor_control_lease_blocks_old_wait_mutation(pg_native):
    connections, run, now, leases, request, service = wait_setup(pg_native)
    first_scope = leases[0].acquire(request)
    first = first_scope.__enter__()
    try:
        old = service(0, first)
        old.arm_timer(TARGET, TimerSpec(duration_seconds=0), timeout_seconds=50)
        snapshot = old._store.load(run)
        now[0] = 131
        with leases[1].acquire(request) as successor:
            assert successor.fencing_token > first.fencing_token
            with pytest.raises(OptimisticConcurrencyError, match='bpmn_control_lease_stale'):
                old.cancel(TARGET)
            assert old._store.load(run) == snapshot
            successor.ensure_valid()
            assert service(1, successor).inspect(TARGET).status == 'ready'
    finally:
        first_scope.__exit__(None, None, None)
        for engine in connections:
            engine.dispose()
