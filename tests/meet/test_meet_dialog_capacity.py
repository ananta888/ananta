"""Independent SQL clients and virtual clocks verify bounded Hub dialog admission."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, insert, select, update

from agent.db_models import TaskDB
from agent.models.meet_dialog_capacity import DialogCapacityPolicy, reservation
from agent.repositories.meet_dialog_capacity import SqlDialogCapacity, pools, slots, tasks
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_capacity import MeetDialogCapacity

pytestmark = pytest.mark.timeout(30)
NOW = 1000.0
PUBLISHER = "http://publisher:8091"


def scope(number=0):
    return SimpleNamespace(
        task_id=f"task-{number}",
        lease_id=f"lease-{number}",
        runtime_id=f"runtime-{number}",
        tenant_id="synthetic",
        project_id="synthetic",
        deadline=2000,
        capabilities=["avatar.publish", "speech.publish", "screen.publish"],
    )


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dialog-capacity.sqlite'}", connect_args={"timeout": 3})
    tasks.create(engine)
    with engine.begin() as connection:
        for number in range(12):
            task = TaskDB(id=scope(number).task_id, status="in_progress", task_kind="meet_dialog_session")
            connection.execute(insert(tasks).values(**task.model_dump()))
    store = SqlDialogCapacity(engine, "synthetic-dialog", DialogCapacityPolicy(sessions=2, publisher_sessions=1))
    store.initialize()
    yield store
    engine.dispose()


def row(store, identity):
    with store.engine.connect() as connection:
        return connection.execute(select(slots).where(slots.c.id == identity)).mappings().one()


def test_cost_reserves_all_capabilities_and_maximum_fanout_without_private_content():
    original = scope()
    original.private_grant = "PRIVATE-MARKER"
    binding = reservation(original, PUBLISHER)
    assert binding["publication_bps"] == (128000 + 1200000 + 2500000) * 19
    assert "PRIVATE-MARKER" not in repr(binding)
    original.capabilities.clear()
    assert reservation(original, PUBLISHER)["publication_bps"] == 0
    assert binding["publication_bps"] > 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("sessions", 0),
        ("sessions", True),
        ("sessions", 33),
        ("publisher_sessions", 3),
        ("publication_bps", 0),
        ("publication_bps", "384000000"),
    ],
)
def test_operator_limits_are_closed_strict_integers(field, value):
    with pytest.raises(ValueError, match="policy_invalid"):
        DialogCapacityPolicy(**{field: value})


def test_total_publisher_and_bandwidth_limits_are_independent():
    first = reservation(scope(), PUBLISHER)
    second = reservation(scope(1), "http://second:8091")
    profile = DialogCapacityPolicy(sessions=2, publisher_sessions=1)
    assert profile.fits([first], second)
    assert not profile.fits([first], first)
    assert not profile.fits([first, second], second | {"publisher": "http://third:8091"})
    assert not replace(profile, publication_bps=first["publication_bps"]).fits([first], second)


def test_fifo_cannot_skip_a_busy_publisher_even_for_an_available_other_worker(repository):
    bindings = [reservation(scope(i), PUBLISHER if i < 2 else "http://second:8091") for i in range(3)]
    ids = [repository.reserve(binding, NOW) for binding in bindings]
    assert repository.poll(ids[0], bindings[0], NOW)
    assert not repository.poll(ids[1], bindings[1], NOW)
    assert not repository.poll(ids[2], bindings[2], NOW)
    repository.cancel_before_dispatch(ids[1], bindings[1], NOW)
    assert repository.poll(ids[2], bindings[2], NOW)


def test_independent_hub_connections_cannot_oversubscribe_publisher(repository):
    other_engine = create_engine(repository.engine.url, connect_args={"timeout": 3})
    other = SqlDialogCapacity(other_engine, repository.pool, repository.policy)
    barrier = Barrier(2)

    def acquire(index):
        store = repository if index == 0 else other
        binding = reservation(scope(index), PUBLISHER)
        barrier.wait(timeout=5)
        identity = store.reserve(binding, NOW)
        return store.poll(identity, binding, NOW)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sum(executor.map(acquire, range(2))) == 1
    finally:
        other_engine.dispose()


def test_duplicate_dispatch_is_fenced_after_release_and_queue_is_bounded(repository):
    bindings = [reservation(scope(i), PUBLISHER) for i in range(5)]
    ids = [repository.reserve(binding, NOW) for binding in bindings[:4]]
    with pytest.raises(MeetError, match="queue_full"):
        repository.reserve(bindings[4], NOW)
    repository.cancel_before_dispatch(ids[0], bindings[0], NOW)
    with pytest.raises(MeetError, match="duplicate_dispatch"):
        repository.reserve(bindings[0], NOW)
    repository.reserve(bindings[4], NOW)


def test_orphan_wait_expires_and_restart_does_not_reset_active_capacity(repository):
    first, second = [reservation(scope(i), PUBLISHER) for i in range(2)]
    old = repository.reserve(first, NOW)
    following = repository.reserve(second, NOW + 5)
    assert not repository.poll(following, second, NOW + 9)
    assert repository.poll(following, second, NOW + 10)
    assert row(repository, old)["status"] == "stale_released"
    repository.initialize()
    assert row(repository, following)["status"] == "active"


def test_terminal_task_reclamation_retains_startup_cleanup_quarantine_across_restart(repository):
    first = reservation(scope(), PUBLISHER)
    identity = repository.reserve(first, NOW)
    assert repository.poll(identity, first, NOW)
    with repository.engine.begin() as connection:
        connection.execute(update(tasks).where(tasks.c.id == first["task_id"]).values(status="cancelled"))
    repository.initialize()
    following = reservation(scope(1), PUBLISHER)
    queued = repository.reserve(following, NOW + 1)
    assert not repository.poll(queued, following, NOW + 1)
    assert row(repository, identity)["deadline_at"] == NOW + 96
    repository.cancel_before_dispatch(queued, following, NOW + 1)
    third = reservation(scope(2), PUBLISHER)
    newest = repository.reserve(third, NOW + 94)
    assert not repository.poll(newest, third, NOW + 95)
    assert repository.poll(newest, third, NOW + 96)
    assert row(repository, identity)["status"] == "stale_released"


def test_changed_profile_is_rejected_on_initialization_and_during_existing_wait(repository):
    changed = SqlDialogCapacity(repository.engine, repository.pool, replace(repository.policy, sessions=1))
    with pytest.raises(MeetError, match="pool_changed"):
        changed.initialize()
    binding = reservation(scope(), PUBLISHER)
    identity = repository.reserve(binding, NOW)
    with repository.engine.begin() as connection:
        connection.execute(update(pools).where(pools.c.id == repository.pool).values(policy_digest="a" * 64))
    with pytest.raises(MeetError, match="pool_changed"):
        repository.poll(identity, binding, NOW)


def test_mutated_active_reservation_cannot_lower_cost_to_admit_more_publishers(repository):
    binding = reservation(scope(), PUBLISHER)
    identity = repository.reserve(binding, NOW)
    assert repository.poll(identity, binding, NOW)
    with repository.engine.begin() as connection:
        connection.execute(
            update(slots).where(slots.c.id == identity).values(lease_metadata=binding | {"publication_bps": 0})
        )
    next_binding = reservation(scope(1), "http://second:8091")
    second = repository.reserve(next_binding, NOW)
    with pytest.raises(MeetError, match="state_changed"):
        repository.poll(second, next_binding, NOW)


@pytest.mark.parametrize("field", ["task_id", "lease_id", "runtime_id", "tenant_id", "project_id", "publisher"])
def test_changed_binding_cannot_poll_or_cancel_another_reservation(repository, field):
    binding = reservation(scope(), PUBLISHER)
    identity = repository.reserve(binding, NOW)
    for method in (repository.poll, repository.cancel_before_dispatch):
        with pytest.raises(MeetError, match="lease_changed"):
            method(identity, binding | {field: "foreign"}, NOW)
    assert row(repository, identity)["status"] == "queued"


class Clock:
    now = NOW

    def read(self):
        return self.now

    def wait(self, seconds):
        assert 0 < seconds <= 0.1
        self.now += seconds


def service(store, current, clock):
    return MeetDialogCapacity(
        store, Mock(current=Mock(return_value=current)), clock=clock.read, monotonic=clock.read, wait=clock.wait
    )


def test_busy_wait_is_bounded_and_never_dispatches_or_retains_queued_reservation(repository):
    current = scope()
    binding = reservation(current, PUBLISHER)
    identity = repository.reserve(binding, NOW)
    repository.poll(identity, binding, NOW)
    clock, operation = Clock(), Mock()
    with pytest.raises(MeetError, match="wait_expired"):
        service(repository, scope(1), clock).dispatch(scope(1), PUBLISHER, operation)
    operation.assert_not_called()
    assert clock.now == pytest.approx(NOW + 10)
    with repository.engine.connect() as connection:
        assert not connection.execute(select(slots).where(slots.c.status == "queued")).first()


@pytest.mark.parametrize("fails", [False, True])
def test_both_acceptance_and_uncertain_dispatch_retain_active_capacity(repository, fails):
    current, operation = scope(), Mock(return_value={"accepted": True})
    if fails:
        operation.side_effect = RuntimeError("synthetic_transport_loss")
        with pytest.raises(RuntimeError, match="synthetic_transport_loss"):
            service(repository, current, Clock()).dispatch(current, PUBLISHER, operation)
    else:
        assert service(repository, current, Clock()).dispatch(current, PUBLISHER, operation) == {"accepted": True}
    operation.assert_called_once()
    with repository.engine.connect() as connection:
        active = connection.execute(select(slots).where(slots.c.status == "active")).mappings().one()
    assert active["deadline_at"] == current.deadline + 5


def test_revocation_after_promotion_never_dispatches_and_releases_undispatched_slot(repository):
    current, operation = scope(), Mock()
    gate = service(repository, current, Clock())
    gate.authority.current.side_effect = [current, current, MeetError("synthetic_revoked", 403)]
    with pytest.raises(MeetError, match="synthetic_revoked"):
        gate.dispatch(current, PUBLISHER, operation)
    operation.assert_not_called()
    with repository.engine.connect() as connection:
        assert not connection.execute(select(slots).where(slots.c.status.in_(("queued", "active")))).first()


def test_slow_final_authority_read_cannot_extend_original_admission_wait(repository):
    current, clock, operation = scope(), Clock(), Mock()
    gate = service(repository, current, clock)
    calls = 0

    def authority_read(*_ids):
        nonlocal calls
        calls += 1
        if calls == 3:
            clock.now += 11
        return current

    gate.authority.current.side_effect = authority_read
    with pytest.raises(MeetError, match="wait_expired"):
        gate.dispatch(current, PUBLISHER, operation)
    operation.assert_not_called()


@pytest.mark.parametrize("value", ["[]", "null", '{"unknown":1}', '{"sessions":true}', '{"publisher_sessions":4}'])
def test_invalid_config_never_creates_resource_pool(monkeypatch, value):
    from agent.bootstrap.meet_dialog_capacity import configured_dialog_capacity

    monkeypatch.setenv("ANANTA_MEET_DIALOG_CAPACITY", value)
    constructor = Mock()
    monkeypatch.setattr("agent.bootstrap.meet_dialog_capacity.SqlDialogCapacity", constructor)
    with pytest.raises(ValueError, match="config_invalid"):
        configured_dialog_capacity(Mock(), Mock(), Mock())
    constructor.assert_not_called()
