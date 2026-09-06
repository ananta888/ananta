"""Two independent Hub admission instances share existing SQL slot leases."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select

from agent.repositories.meet_capacity import SqlMeetCapacity, slots
from agent.services.meet_capacity_admission import MeetCapacityAdmission
from agent.services.meet_contract import MeetError

pytestmark = pytest.mark.timeout(30)
NOW = 1000.0


def turn(number=1):
    return {
        "task_id": f"task-{number}",
        "lease_id": f"lease-{number}",
        "tenant_id": "synthetic",
        "project_id": "synthetic",
        "deadline": 1115,
        "text": "PRIVATE-MARKER",
        "meeting": {"grant": "PRIVATE-MARKER"},
    }


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'capacity.sqlite'}", connect_args={"timeout": 3})
    store = SqlMeetCapacity(engine, "synthetic-media")
    store.initialize()
    yield store
    engine.dispose()


def row(repository, identity):
    with repository.engine.connect() as connection:
        return connection.execute(select(slots).where(slots.c.id == identity)).mappings().one()


def test_fifo_without_queue_jumping_and_no_private_content(repository):
    first, second, third = [repository.reserve(turn(i), NOW) for i in range(3)]
    assert not repository.poll(third, turn(2), NOW)
    assert repository.poll(first, turn(0), NOW)
    assert not repository.poll(second, turn(1), NOW)
    repository.finish(first, turn(0), NOW)
    assert not repository.poll(third, turn(2), NOW)
    assert repository.poll(second, turn(1), NOW)
    assert "PRIVATE-MARKER" not in repr(row(repository, second))
    assert row(repository, second)["parent_task_id"] == "task-1"


def test_concurrent_independent_hubs_cannot_double_admit(repository):
    other_engine = create_engine(repository.engine.url, connect_args={"timeout": 3})
    other = SqlMeetCapacity(other_engine, repository.pool)
    barrier = Barrier(2)

    def acquire(index):
        store = repository if index == 0 else other
        barrier.wait(timeout=5)
        identity = store.reserve(turn(index), NOW)
        return identity, store.poll(identity, turn(index), NOW)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(acquire, range(2)))
        assert sum(active for _identity, active in outcomes) == 1
        assert len({identity for identity, _active in outcomes}) == 2
    finally:
        other_engine.dispose()


def test_queue_is_bounded_and_duplicate_dispatch_is_never_reused(repository):
    first = repository.reserve(turn(), NOW)
    for _ in range(2):
        with pytest.raises(MeetError, match="duplicate_dispatch"):
            repository.reserve(turn(), NOW)
    assert repository.poll(first, turn(), NOW)
    for number in range(2, 6):
        repository.reserve(turn(number), NOW)
    with pytest.raises(MeetError, match="queue_full"):
        repository.reserve(turn(6), NOW)
    repository.finish(first, turn(), NOW)
    with pytest.raises(MeetError, match="duplicate_dispatch"):
        repository.reserve(turn(), NOW)


def test_scope_replacement_cannot_poll_or_release_foreign_capacity(repository):
    identity = repository.reserve(turn(), NOW)
    for field, value in [
        ("tenant_id", "other"),
        ("project_id", "other"),
        ("task_id", "other"),
        ("lease_id", "other"),
        ("deadline", 1116),
    ]:
        for method in [repository.poll, repository.finish]:
            with pytest.raises(MeetError, match="lease_changed"):
                method(identity, turn() | {field: value}, NOW)
    assert row(repository, identity)["status"] == "queued"


def test_crashed_waiter_expires_after_ten_seconds_without_human_cleanup(repository):
    abandoned = repository.reserve(turn(), NOW)
    successor = repository.reserve(turn(2), NOW + 5)
    assert not repository.poll(successor, turn(2), NOW + 9)
    assert repository.poll(successor, turn(2), NOW + 10)
    assert row(repository, abandoned)["status"] == "stale_released"


def test_uncertain_execution_keeps_capacity_until_worker_deadline_plus_grace(repository):
    original = repository.reserve(turn(), NOW)
    assert repository.poll(original, turn(), NOW)
    repository.finish(original, turn(), NOW + 1, uncertain=True)
    assert row(repository, original)["status"] == "active"
    assert row(repository, original)["reason_code"] == "meet_capacity_execution_uncertain"
    next_turn = turn(2) | {"deadline": 1200}
    successor = repository.reserve(next_turn, 1119)
    assert not repository.poll(successor, next_turn, 1119)
    assert repository.poll(successor, next_turn, 1120)


def test_reinitialization_preserves_live_capacity_and_fencing(repository):
    original = repository.reserve(turn(), NOW)
    assert repository.poll(original, turn(), NOW)
    restarted = SqlMeetCapacity(repository.engine, repository.pool)
    restarted.initialize()
    successor = restarted.reserve(turn(2), NOW)
    assert not restarted.poll(successor, turn(2), NOW)


class Clock:
    now = NOW

    def read(self):
        return self.now

    def wait(self, duration):
        assert 0 < duration <= 0.1
        self.now += duration


def admission(repository, clock, tasks=None):
    return MeetCapacityAdmission(repository, tasks or Mock(), clock=clock.read, monotonic=clock.read, wait=clock.wait)


def test_wait_budget_is_bounded_and_never_dispatches_busy_worker(repository):
    original = repository.reserve(turn(), NOW)
    repository.poll(original, turn(), NOW)
    clock, operation = Clock(), Mock()
    with pytest.raises(MeetError, match="wait_expired"):
        admission(repository, clock).run(turn(2), operation, Mock())
    assert clock.now == pytest.approx(NOW + 10)
    operation.assert_not_called()


def test_policy_revocation_during_wait_cancels_reservation(repository):
    original = repository.reserve(turn(), NOW)
    repository.poll(original, turn(), NOW)
    policy, operation = Mock(), Mock()
    policy.side_effect = [None, None, MeetError("synthetic_policy_revoked", 403)]
    with pytest.raises(MeetError, match="synthetic_policy_revoked"):
        admission(repository, Clock()).run(turn(2), operation, policy)
    operation.assert_not_called()
    with repository.engine.connect() as connection:
        assert not connection.execute(select(slots.c.id).where(slots.c.status == "queued")).first()


def test_cancelled_hub_task_never_gets_a_capacity_slot(repository):
    tasks, operation = Mock(), Mock()
    tasks.require_current.side_effect = MeetError("meet_capacity_task_changed", 409)
    with pytest.raises(MeetError, match="task_changed"):
        admission(repository, Clock(), tasks).run(turn(), operation, Mock())
    operation.assert_not_called()
    with repository.engine.connect() as connection:
        assert not connection.execute(select(slots.c.id)).first()


def test_success_releases_but_worker_failure_quarantines(repository):
    service = admission(repository, Clock())
    assert service.run(turn(), lambda: "ok", Mock()) == "ok"
    with repository.engine.connect() as connection:
        assert connection.execute(select(slots.c.status)).scalar_one() == "released"
    with pytest.raises(TimeoutError):
        service.run(turn(2), Mock(side_effect=TimeoutError), Mock())
    with repository.engine.connect() as connection:
        assert len(connection.execute(select(slots.c.id).where(slots.c.status == "active")).all()) == 1


def test_post_execution_revocation_hides_success_but_releases_finished_worker(repository):
    policy = Mock(side_effect=[None, None, None, MeetError("synthetic_policy_revoked", 403)])
    with pytest.raises(MeetError, match="synthetic_policy_revoked"):
        admission(repository, Clock()).run(turn(), lambda: "private-result", policy)
    with repository.engine.connect() as connection:
        assert connection.execute(select(slots.c.status)).scalar_one() == "released"
