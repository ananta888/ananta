"""Durable Hub resource admission; no speech or production evidence is simulated."""

import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select

from agent.models.meet_speaker_floor import CLEANUP_MS, OUTPUT_MS, WAIT_MS, SpeakerTurn
from agent.repositories.meet_speaker_floor import SqlMeetSpeakerFloor, turns
from agent.services.meet_contract import MeetError

pytestmark = pytest.mark.timeout(30)
NOW = 1_000_000


def turn(number=1, *, priority=0, **changes):
    binding = {
        "tenant_id": "synthetic",
        "project_id": "synthetic",
        "task_id": f"task-{number}",
        "lease_id": f"dispatch-{number}",
        "runtime_id": f"runtime-{number}",
        "session_id": f"session-{number}",
        "own_peer_id": f"machine-{number}",
        "sender_peer_id": "human",
        "generation": 1,
        "membership_epoch": 2,
        "receive_revision": 3,
        "chat_revision": 1,
        "speech_revision": 1,
        "deadline_ms": NOW + 120_000,
        "meet_session_id": "ms_" + "a" * 32,
        "room_id": "room-" + "b" * 18,
    } | changes
    return SpeakerTurn.from_binding("https://meet.invalid", binding, f"turn-{number}", priority=priority)


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'speaker.sqlite'}", connect_args={"timeout": 5})
    result = SqlMeetSpeakerFloor(engine)
    result.initialize()
    yield result
    engine.dispose()


def rows(store):
    with store.engine.connect() as connection:
        return connection.execute(select(turns).order_by(turns.c.sequence)).mappings().all()


def test_fifo_serializes_output_not_only_generation_and_waits_for_quiet(store):
    a, b, c = turn(1), turn(2), turn(3)
    for item in (a, b, c):
        store.reserve(item, NOW)
    assert store.poll(c, NOW) is None
    permit = store.poll(a, NOW)
    assert permit == {"id": a.identity, "sequence": 1, "expires_ms": NOW + OUTPUT_MS}
    assert store.poll(b, NOW) is None
    store.cancel(a, NOW + 1000)
    assert not store.current(a, permit, NOW + 1000)
    assert store.poll(b, NOW + 1000 + CLEANUP_MS - 1) is None
    assert store.poll(c, NOW + 1000 + CLEANUP_MS) is None
    assert store.poll(b, NOW + 1000 + CLEANUP_MS)["sequence"] == 2
    # A late callback for A cannot withdraw B or move its deadline.
    store.cancel(a, NOW + 6000)
    assert rows(store)[1]["state"] == "active"


def test_priority_is_bounded_hub_policy_and_aging_prevents_queue_jumping(store):
    low, high = turn(1), turn(2, priority=2)
    store.reserve(low, NOW)
    store.reserve(high, NOW + 1000)
    assert store.poll(low, NOW + 1000) is None
    # After six seconds low has the same effective priority and wins FIFO.
    assert store.poll(high, NOW + 6000) is None
    assert store.poll(low, NOW + 6000)["sequence"] == 1


def test_higher_policy_priority_wins_before_aging_threshold(store):
    low, high = turn(1), turn(2, priority=2)
    store.reserve(low, NOW)
    store.reserve(high, NOW + 1)
    assert store.poll(high, NOW + 1)["sequence"] == 2
    assert store.poll(low, NOW + 1) is None


def test_two_independent_hub_connections_cannot_grant_simultaneously(store):
    other_engine = create_engine(store.engine.url, connect_args={"timeout": 5})
    other = SqlMeetSpeakerFloor(other_engine)
    barrier = Barrier(2)

    def acquire(number):
        repository = store if number == 1 else other
        barrier.wait(timeout=5)
        repository.reserve(turn(number), NOW)
        return repository.poll(turn(number), NOW)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(acquire, (1, 2)))
        assert sum(result is not None for result in results) == 1
        assert len({row["sequence"] for row in rows(store)}) == 2
    finally:
        other_engine.dispose()


def test_restart_preserves_active_permit_and_does_not_extend_output(store):
    item = turn()
    store.reserve(item, NOW)
    permit = store.poll(item, NOW)
    restarted = SqlMeetSpeakerFloor(store.engine)
    restarted.initialize()
    assert restarted.poll(item, NOW + 5000) == permit
    assert restarted.current(item, permit, NOW + 5000)
    assert not restarted.current(item, permit, permit["expires_ms"])
    assert rows(store)[0]["state"] == "quarantine"
    assert not restarted.current(item, permit, permit["expires_ms"] + CLEANUP_MS)
    assert rows(store)[0]["state"] == "finished"
    with pytest.raises(MeetError, match="inactive"):
        restarted.poll(item, NOW + 70_000)


def test_lost_completion_holds_floor_through_original_deadline_and_cleanup(store):
    a = turn()
    store.reserve(a, NOW)
    permit = store.poll(a, NOW)
    b = turn(2)
    store.reserve(b, permit["expires_ms"] - 1000)
    assert store.poll(b, permit["expires_ms"]) is None
    assert store.poll(b, permit["expires_ms"] + CLEANUP_MS - 1) is None
    assert store.poll(b, permit["expires_ms"] + CLEANUP_MS)["sequence"] == 2


def test_waiting_is_bounded_and_cancelled_turn_cannot_replay(store):
    for number in range(4):
        store.reserve(turn(number), NOW)
    with pytest.raises(MeetError, match="waiters_full"):
        store.reserve(turn(5), NOW)
    with pytest.raises(MeetError, match="inactive"):
        store.poll(turn(0), NOW + WAIT_MS)
    store.cancel(turn(0), NOW + WAIT_MS)
    with pytest.raises(MeetError, match="duplicate_turn"):
        store.reserve(turn(0), NOW + WAIT_MS)
    store.reserve(turn(5), NOW + WAIT_MS)
    assert store.poll(turn(5), NOW + WAIT_MS)["sequence"] == 5


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "project_id",
        "task_id",
        "lease_id",
        "runtime_id",
        "session_id",
        "sender_peer_id",
        "own_peer_id",
        "meet_session_id",
        "room_id",
        "generation",
        "membership_epoch",
        "receive_revision",
        "chat_revision",
        "speech_revision",
        "deadline_ms",
    ],
)
def test_each_authority_binding_substitution_cannot_access_or_cancel_permit(store, field):
    item = turn()
    store.reserve(item, NOW)
    permit = store.poll(item, NOW)
    old = item.binding[field]
    changed = (
        old + 1
        if type(old) is int
        else "ms_" + "c" * 32
        if field == "meet_session_id"
        else "room-" + "c" * 18
        if field == "room_id"
        else "foreign"
    )
    foreign = turn(**{field: changed})
    for operation in (lambda: store.current(foreign, permit, NOW), lambda: store.cancel(foreign, NOW)):
        with pytest.raises(MeetError, match="turn_changed"):
            operation()
    assert store.current(item, permit, NOW)


def test_same_physical_room_has_one_floor_across_tenants_and_projects(store):
    a, b = turn(), turn(2, tenant_id="another", project_id="another")
    assert a.room_key == b.room_key and a.identity != b.identity
    store.reserve(a, NOW)
    store.reserve(b, NOW)
    store.poll(a, NOW)
    assert store.poll(b, NOW) is None


def test_distinct_physical_rooms_do_not_block_each_other(store):
    a, b = turn(), turn(2, room_id="room-" + "c" * 18)
    for item in (a, b):
        store.reserve(item, NOW)
        assert store.poll(item, NOW) is not None


def test_priority_mutation_cannot_replay_an_input_and_metadata_is_content_free(store):
    item = turn()
    store.reserve(item, NOW)
    changed = replace(item, priority=2)
    assert changed.identity == item.identity
    with pytest.raises(MeetError, match="duplicate_turn"):
        store.reserve(changed, NOW)
    with pytest.raises(MeetError, match="turn_changed"):
        store.poll(changed, NOW)
    encoded = json.dumps([dict(row) for row in rows(store)])
    for marker in ("https://", "human", "ms_", "room-", "speech_revision", "pcm", "text"):
        assert marker not in encoded
    assert rows(store)[0]["binding"]["task_id"] == item.binding["task_id"]


def test_original_authority_deadline_is_never_extended(store):
    item = turn(deadline_ms=NOW + 2)
    store.reserve(item, NOW)
    assert store.poll(item, NOW)["expires_ms"] == NOW + 2
    expired = turn(2, deadline_ms=NOW)
    with pytest.raises(MeetError, match="turn_expired"):
        store.reserve(expired, NOW)


@pytest.mark.parametrize("priority", [-1, 3, True, 1.0, "1"])
def test_unbounded_or_noninteger_priorities_are_rejected(priority):
    with pytest.raises(ValueError, match="turn_invalid"):
        turn(priority=priority)


@pytest.mark.parametrize("now", [True, 0, -1, 1.5, float("nan"), float("inf"), 2**53])
def test_nonfinite_or_ambiguous_clock_is_rejected_without_writing(store, now):
    with pytest.raises(ValueError, match="clock_invalid"):
        store.reserve(turn(), now)
    assert rows(store) == []


def test_immutable_turn_does_not_share_mutable_binding():
    item = turn()
    binding = item.binding
    binding["task_id"] = "foreign"
    assert item.binding["task_id"] == "task-1"
    with pytest.raises(FrozenInstanceError):
        item.priority = 2


@pytest.mark.parametrize(
    "change",
    [
        {"id": "foreign"},
        {"sequence": True},
        {"expires_ms": 1060000.0},
        {"extra": True},
        {"sequence": 2},
        {"expires_ms": NOW + OUTPUT_MS + 1},
    ],
)
def test_forged_permit_does_not_receive_current_authority(store, change):
    item = turn()
    store.reserve(item, NOW)
    permit = store.poll(item, NOW)
    assert not store.current(item, permit | change, NOW)
    assert store.current(item, permit, NOW)


def _process_poll(url, number, barrier, result):
    engine = create_engine(url, connect_args={"timeout": 5})
    try:
        repository = SqlMeetSpeakerFloor(engine)
        repository.reserve(turn(number), NOW)
        barrier.wait(timeout=10)
        result.put((os.getpid(), repository.poll(turn(number), NOW)))
    finally:
        engine.dispose()


def test_actual_separate_hub_processes_share_one_room_floor(store, monkeypatch):
    # The broad app fixture adds dependencies with their own `tests` package.
    # Fresh interpreters must select this checkout, as in dialog-start tests.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    context = multiprocessing.get_context("spawn")
    barrier, result = context.Barrier(2), context.Queue()
    children = [context.Process(target=_process_poll, args=(str(store.engine.url), i, barrier, result)) for i in (1, 2)]
    try:
        for child in children:
            child.start()
        outcomes = [result.get(timeout=12) for _ in children]
        for child in children:
            child.join(timeout=2)
            assert child.exitcode == 0
        assert len({pid for pid, _permit in outcomes}) == 2
        assert os.getpid() not in {pid for pid, _permit in outcomes}
        assert sum(permit is not None for _pid, permit in outcomes) == 1
    finally:
        for child in children:
            if child.pid is not None and child.is_alive():
                child.kill()
                child.join(timeout=2)
            if child.pid is not None:
                assert not child.is_alive(), "owned speaker probe cleanup failed"
                child.close()
        result.close()
        result.join_thread()
