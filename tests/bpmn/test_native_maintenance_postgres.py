"""Owned PostgreSQL cadence and generic Native control-lease concurrency."""

# ruff: noqa: F811 -- the owned PostgreSQL fixture is deliberately imported.

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlmodel import Session, select

from agent.db_models.native_maintenance import NativeMaintenanceCadenceDB
from agent.repositories.native_maintenance_cadence import SQLMaintenanceCadenceRepository
from agent.services.bpmn_run_lease import BpmnRunLeaseService, is_bpmn_plan
from agent.services.native_result_maintenance_policy import configured_maintenance_schedules, maintenance_request
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.security import HmacKeyRing
from agent.services.workflow_runtime.sqlalchemy_event_stores import SQLAlchemyCheckpointStore
from tests.bpmn.test_native_postgres_concurrency import engines
from tests.bpmn.test_native_postgres_concurrency import pg_native as pg_native
from tests.bpmn.test_result_maintenance_cadence import policy


def test_postgres_parallel_cadence_hubs_reserve_one_exact_run(pg_native):
    connections = engines(pg_native)
    schedule, = configured_maintenance_schedules(policy())
    start = Barrier(2, timeout=5)

    def reserve(engine):
        start.wait()
        return SQLMaintenanceCadenceRepository(engine).reserve(schedule, now=100)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.map(reserve, connections)
        assert a == b
        with Session(pg_native.engine) as session:
            row, = session.exec(select(NativeMaintenanceCadenceDB)).all()
            assert row.ordinal == 1 and row.current_run_id == a.run_id
    finally:
        for connection in connections:
            connection.dispose()


def test_postgres_maintenance_uses_hub_fence_without_claiming_bpmn_definition(pg_native):
    connections = engines(pg_native)
    schedule, = configured_maintenance_schedules(policy())
    request = maintenance_request(schedule, 'postgres-maintenance-control-run')
    assert not is_bpmn_plan(request.plan)
    keys = HmacKeyRing({'synthetic': b'synthetic-maintenance-control-key'}, active_key_id='synthetic')
    leases = [BpmnRunLeaseService(checkpoints=SQLAlchemyCheckpointStore(connection), keys=keys, clock=lambda: 100)
              for connection in connections]
    try:
        with leases[0].acquire(request) as first:
            assert first is not None
            first.ensure_valid()
            with pytest.raises(OptimisticConcurrencyError, match='control_lease_held'):
                with leases[1].acquire(request):
                    pytest.fail('Parallel Hub acquired the active maintenance Run fence')
        with leases[1].acquire(request) as successor:
            assert successor.fencing_token > first.fencing_token
    finally:
        for connection in connections:
            connection.dispose()
