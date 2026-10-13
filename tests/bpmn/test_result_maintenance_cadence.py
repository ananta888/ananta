"""Durable finite Hub cadence; synthetic policy fixtures never imply release evidence."""

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models.native_maintenance import NativeMaintenanceCadenceDB
from agent.repositories.native_maintenance_cadence import SQLMaintenanceCadenceRepository
from agent.services.native_result_maintenance_cadence import NativeResultMaintenanceCadence
from agent.services.native_result_maintenance_policy import configured_maintenance_schedules, maintenance_request


def policy():
    return {'enabled': True, 'auto_approval': True, 'policy_revision': 'synthetic-policy-v1', 'schedules': [{
        'schedule_id': 'synthetic-schedule', 'tenant_id': 'tenant-a', 'project_id': 'synthetic-project',
        'control_task_id': 'synthetic-control', 'owner': {'worker_id': 'synthetic-worker',
            'worker_url': 'http://synthetic-worker:5000', 'store_binding_id': 'synthetic-store-v1'},
        'interval_seconds': 600, 'batch_size': 100}]}


@pytest.fixture
def cadence(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cadence.sqlite'}")
    SQLModel.metadata.create_all(engine)
    now, configuration = [100.0], [policy()]
    store = SQLMaintenanceCadenceRepository(engine)
    calls, cancellations = [], []
    state = SimpleNamespace(status='running', denied=False, crash=False)

    class Admission:
        def require(self, schedule):
            if state.denied:
                raise PermissionError('synthetic_policy_denied')

    class Runs:
        def advance(self, reservation):
            calls.append(reservation)
            if state.crash:
                raise RuntimeError('synthetic_unknown_outcome')
            return SimpleNamespace(status=state.status)

        def cancel(self, reservation):
            cancellations.append(reservation)

    def service():
        return NativeResultMaintenanceCadence(store=SQLMaintenanceCadenceRepository(engine), runs=Runs(),
            admission=Admission(), configuration=lambda: configuration[0], clock=lambda: now[0])

    yield SimpleNamespace(engine=engine, now=now, configuration=configuration, store=store, calls=calls,
                          cancellations=cancellations, state=state, service=service)
    engine.dispose()


def test_restart_reuses_exact_pending_run_and_next_interval_creates_one_new_run(cadence):
    result = cadence.service().run_once()
    assert result['reserved'] == result['active'] == 1
    first = cadence.calls[-1]
    assert cadence.service().run_once()['active'] == 1 and cadence.calls[-1] == first
    cadence.state.status = 'completed'
    assert cadence.service().run_once()['completed'] == 1 and cadence.store.pending() == ()
    assert cadence.service().run_once()['reserved'] == 0
    cadence.now[0] = 700
    assert cadence.service().run_once()['completed'] == 1 and cadence.calls[-1].run_id != first.run_id


def test_unknown_run_outcome_does_not_create_a_replacement(cadence):
    cadence.state.crash = True
    assert cadence.service().run_once()['blocked'] == 1
    pending, = cadence.store.pending()
    cadence.now[0] += 1000
    assert cadence.service().run_once()['blocked'] == 1 and cadence.store.pending() == (pending,)
    assert all(call == pending for call in cadence.calls)


@pytest.mark.parametrize('change', ['disabled', 'removed', 'revision', 'store', 'approval_withdrawn', 'malformed'])
def test_withdrawn_policy_cancels_the_persisted_old_run_before_another_reservation(cadence, change):
    cadence.service().run_once()
    old, = cadence.store.pending()
    if change == 'disabled':
        cadence.configuration[0]['enabled'] = False
    elif change == 'removed':
        cadence.configuration[0] = None
    elif change == 'revision':
        cadence.configuration[0]['policy_revision'] = 'synthetic-policy-v2'
    elif change == 'approval_withdrawn':
        cadence.configuration[0]['auto_approval'] = False
    elif change == 'malformed':
        cadence.configuration[0] = {'enabled': 'invalid'}
    else:
        cadence.configuration[0]['schedules'][0]['owner']['store_binding_id'] = 'other-store'
    result = cadence.service().run_once()
    assert result['cancelled'] == 1 and result['reserved'] == 0
    assert cadence.cancellations == [old] and cadence.store.pending() == ()


def test_revoked_admission_never_reserves_and_cancels_an_existing_run(cadence):
    cadence.state.denied = True
    assert cadence.service().run_once()['blocked'] == 1 and cadence.store.pending() == () and cadence.calls == []
    cadence.state.denied = False
    cadence.service().run_once()
    cadence.state.denied = True
    result = cadence.service().run_once()
    assert result['cancelled'] == result['blocked'] == 1


def test_independent_sql_engines_concurrently_reserve_one_identical_run(cadence):
    schedule, = configured_maintenance_schedules(cadence.configuration[0])
    engines = [create_engine(cadence.engine.url) for _ in range(2)]
    barrier = Barrier(2, timeout=5)

    def reserve(engine):
        barrier.wait()
        return SQLMaintenanceCadenceRepository(engine).reserve(schedule, now=100)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            reservations = list(pool.map(reserve, engines))
        assert reservations[0] == reservations[1]
        with Session(cadence.engine) as session:
            row, = session.exec(select(NativeMaintenanceCadenceDB)).all()
            assert row.ordinal == 1
    finally:
        for engine in engines:
            engine.dispose()


def test_stale_terminal_recorder_cannot_clear_a_new_run(cadence):
    schedule, = configured_maintenance_schedules(cadence.configuration[0])
    first = cadence.store.reserve(schedule, now=100)
    cadence.store.finish(first, status='completed', now=101)
    second = cadence.store.reserve(schedule, now=700)
    with pytest.raises(ValueError, match='receipt_stale'):
        cadence.store.finish(first, status='cancelled', now=701)
    assert cadence.store.pending() == (second,)


def test_manifest_tampering_is_refused_and_not_overwritten(cadence):
    cadence.service().run_once()
    with Session(cadence.engine) as session, session.begin():
        row = session.exec(select(NativeMaintenanceCadenceDB)).one()
        manifest = copy.deepcopy(row.policy_manifest)
        manifest['project_id'] = 'foreign-project'
        row.policy_manifest = manifest
        session.add(row)
    result = cadence.service().run_once()
    assert result['blocked'] == 1 and len(cadence.calls) == 1


@pytest.mark.parametrize('changes', [{'auto_approval': False}, {'auto_approval': 1}, {'enabled': 'true'},
    {'schedules': []}, {'policy_revision': None}, {'unknown': True}])
def test_policy_denials_are_bounded_and_never_create_work(cadence, changes):
    cadence.configuration[0].update(changes)
    assert cadence.service().run_once()['blocked'] == 1 and cadence.calls == [] and cadence.store.pending() == ()


def test_default_disabled_policy_is_idle_and_has_no_worker_work(cadence):
    cadence.configuration[0] = None
    assert not any(cadence.service().run_once().values()) and cadence.calls == []


def test_generated_plan_is_finite_bound_and_requires_hub_run_lease(cadence):
    schedule, = configured_maintenance_schedules(cadence.configuration[0])
    request = maintenance_request(schedule, 'synthetic-reserved-run')
    assert request.plan.metadata['hub_control_lease_required'] is True
    assert len(request.plan.nodes) == 1 and request.plan.nodes[0].allowed_tools == ('native.result.expire',)
    assert request.plan.nodes[0].budget.max_attempts == 1 and request.input_data['result_store_owner'] == schedule.owner
