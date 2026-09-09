"""Real SQL recovery resource races/replay; no grant or execution authority."""

import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select, update

from agent.models.meet_dialog_recovery import RecoveryMembership, RecoveryOwner
from agent.repositories.meet_dialog_recovery import SqlDialogRecovery, recoveries
from agent.services.meet_contract import MeetError

NOW = 100_000
OWNER = RecoveryOwner("task", "a" * 64, NOW + 600_000)
MEMBER = RecoveryMembership("ms_" + "a" * 32, "a" * 16, 1, 3, NOW + 120_000, NOW + 7_200_000)
pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.sqlite'}", connect_args={"timeout": 3})
    value = SqlDialogRecovery(engine)
    value.initialize()
    value.observe(OWNER, MEMBER, NOW)
    yield value
    engine.dispose()


def row(store):
    with store.engine.connect() as connection:
        return dict(connection.execute(select(recoveries)).mappings().one())


def joining(store, member=MEMBER, now=NOW, attempt=1):
    first = store.reserve(OWNER, member.session_id, now)
    assert first == {"attempt": attempt, "state": "retiring", "deadline_ms": now + 30000, "ready_ms": 0}
    waiting = store.retired(OWNER, attempt, member.session_id, now + 10)
    assert waiting["ready_ms"] == now + 4010
    assert store.retired(OWNER, attempt, member.session_id, now + 20) == waiting
    value = store.take_grant(OWNER, attempt, member.session_id, now + 4010)
    assert value["state"] == "joining"
    return value


def replacement(letter="b", epoch=5):
    return replace(MEMBER, session_id="ms_" + letter * 32, peer_id=letter * 16, epoch=epoch)


def test_original_membership_and_three_short_lease_refreshes_remain_monotone(store):
    for generation in range(2, 5):
        member = replace(
            MEMBER,
            generation=generation,
            expires_ms=MEMBER.expires_ms + generation * 1000,
            epoch=MEMBER.epoch + generation,
        )
        assert store.observe(OWNER, member, NOW + generation)["state"] == "active"
    assert row(store)["attempt"] == 0
    restarted = SqlDialogRecovery(store.engine)
    with pytest.raises(MeetError, match="membership_changed"):
        restarted.observe(OWNER, MEMBER, NOW + 10)


def test_complete_two_recoveries_keep_original_deadline_and_never_reuse_a_grant_slot(store):
    joining(store)
    with pytest.raises(MeetError, match="grant_unavailable"):
        store.take_grant(OWNER, 1, MEMBER.session_id, NOW + 4011)
    next_member = replacement()
    assert store.observe(OWNER, next_member, NOW + 5000) == {
        "state": "active",
        "attempt": 1,
        "deadline_ms": 0,
        "ready_ms": 0,
    }
    joining(SqlDialogRecovery(store.engine), next_member, NOW + 6000, 2)
    final_member = replacement("c", 7)
    store.observe(OWNER, final_member, NOW + 11000)
    with pytest.raises(MeetError, match="budget_exhausted"):
        store.reserve(OWNER, final_member.session_id, NOW + 12000)
    snapshot = row(store)
    assert snapshot["attempt"] == 2 and snapshot["deadline_ms"] == OWNER.deadline_ms
    assert snapshot["retired"] == [
        {"session_id": MEMBER.session_id, "peer_id": MEMBER.peer_id},
        {"session_id": next_member.session_id, "peer_id": next_member.peer_id},
    ]
    assert not any(key in snapshot for key in ("grant", "transcript", "pcm", "key"))


@pytest.mark.parametrize("stage", ["retiring", "waiting", "joining", "active"])
def test_old_observation_cannot_restore_retired_membership_at_any_recovery_stage(store, stage):
    store.reserve(OWNER, MEMBER.session_id, NOW)
    if stage != "retiring":
        store.retired(OWNER, 1, MEMBER.session_id, NOW + 1)
    if stage in {"joining", "active"}:
        store.take_grant(OWNER, 1, MEMBER.session_id, NOW + 4001)
    if stage == "active":
        store.observe(OWNER, replacement(), NOW + 5000)
    before = row(store)
    with pytest.raises(MeetError):
        store.observe(OWNER, MEMBER, NOW + 6000)
    assert row(store) == before


@pytest.mark.parametrize(
    "change",
    [
        {"session_id": MEMBER.session_id},
        {"peer_id": MEMBER.peer_id},
        {"epoch": 3},
        {"epoch": 2},
        {"generation": 2},
    ],
)
def test_replacement_requires_new_session_peer_later_epoch_and_first_generation(store, change):
    joining(store)
    with pytest.raises(MeetError, match="membership_changed"):
        store.observe(OWNER, replace(replacement(), **change), NOW + 5000)
    assert row(store)["state"] == "joining"


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "foreign"),
        ("assignment_digest", "b" * 64),
        ("deadline_ms", OWNER.deadline_ms + 1),
    ],
)
def test_wrong_task_assignment_or_extended_deadline_cannot_mutate_resource(store, field, value):
    before = row(store)
    with pytest.raises(MeetError):
        store.reserve(replace(OWNER, **{field: value}), MEMBER.session_id, NOW + 1)
    assert row(store) == before


@pytest.mark.parametrize("operation", ["reserve", "retired", "take_grant", "pending", "observe"])
def test_clock_rollback_is_persistently_terminal_even_after_clock_is_restored(store, operation):
    joining(store)
    store.pending(OWNER, 1, MEMBER.session_id, NOW + 5000)
    with pytest.raises(MeetError, match="recovery_expired"):
        if operation == "observe":
            store.observe(OWNER, replacement(), NOW + 4999)
        elif operation == "reserve":
            store.reserve(OWNER, MEMBER.session_id, NOW + 4999)
        else:
            getattr(store, operation)(OWNER, 1, MEMBER.session_id, NOW + 4999)
    assert row(store)["state"] == "failed"
    with pytest.raises(MeetError, match="recovery_expired"):
        SqlDialogRecovery(store.engine).pending(OWNER, 1, MEMBER.session_id, NOW + 5001)


@pytest.mark.parametrize("stage", ["retiring", "waiting", "joining"])
def test_original_attempt_window_expires_without_late_ack_or_restart_extension(store, stage):
    store.reserve(OWNER, MEMBER.session_id, NOW)
    if stage != "retiring":
        store.retired(OWNER, 1, MEMBER.session_id, NOW + 1)
    if stage == "joining":
        store.take_grant(OWNER, 1, MEMBER.session_id, NOW + 4001)
    with pytest.raises(MeetError, match="recovery_expired"):
        store.pending(OWNER, 1, MEMBER.session_id, NOW + 30000)
    assert row(store)["state"] == "failed"
    with pytest.raises(MeetError):
        store.observe(OWNER, replacement(), NOW + 29999)


def test_expired_assignment_observation_commits_terminal_state_even_with_expired_media(store):
    with pytest.raises(MeetError, match="recovery_expired"):
        store.observe(OWNER, MEMBER, OWNER.deadline_ms)
    assert row(store)["state"] == "failed"
    with pytest.raises(MeetError):
        store.observe(OWNER, MEMBER, NOW)


def test_quarantine_starts_at_confirmed_retirement_not_request_and_polls_do_not_extend_it(store):
    store.reserve(OWNER, MEMBER.session_id, NOW)
    for now in (NOW, NOW + 1000, NOW + 5000):
        with pytest.raises(MeetError, match="grant_unavailable"):
            store.take_grant(OWNER, 1, MEMBER.session_id, now)
    store.retired(OWNER, 1, MEMBER.session_id, NOW + 5001)
    for now in (NOW + 5001, NOW + 9000):
        assert store.pending(OWNER, 1, MEMBER.session_id, now)["ready_ms"] == NOW + 9001
        with pytest.raises(MeetError):
            store.take_grant(OWNER, 1, MEMBER.session_id, now)
    assert store.take_grant(OWNER, 1, MEMBER.session_id, NOW + 9001)["state"] == "joining"


@pytest.mark.parametrize(
    "attempt,session",
    [
        (True, MEMBER.session_id),
        (1.0, MEMBER.session_id),
        (0, MEMBER.session_id),
        (2, MEMBER.session_id),
        (1, "ms_" + "b" * 32),
    ],
)
def test_wrong_attempt_and_foreign_session_never_acknowledge_retirement(store, attempt, session):
    store.reserve(OWNER, MEMBER.session_id, NOW)
    with pytest.raises(MeetError, match="attempt_changed"):
        store.retired(OWNER, attempt, session, NOW + 1)
    assert row(store)["state"] == "retiring"


def test_late_retirement_and_pending_ack_cannot_replace_current_membership(store):
    joining(store)
    store.observe(OWNER, replacement(), NOW + 5000)
    for method in (store.retired, store.pending, store.take_grant):
        with pytest.raises(MeetError):
            method(OWNER, 1, MEMBER.session_id, NOW + 6000)
    assert row(store)["membership"] == replacement().metadata


@pytest.mark.parametrize("operation", ["reserve", "take_grant"])
def test_two_independent_hub_stores_race_for_exactly_one_attempt_or_grant(store, operation):
    if operation == "take_grant":
        store.reserve(OWNER, MEMBER.session_id, NOW)
        store.retired(OWNER, 1, MEMBER.session_id, NOW)
    barrier = Barrier(2)

    def compete():
        other = SqlDialogRecovery(store.engine)
        barrier.wait(timeout=3)
        try:
            if operation == "reserve":
                other.reserve(OWNER, MEMBER.session_id, NOW + 4000)
            else:
                other.take_grant(OWNER, 1, MEMBER.session_id, NOW + 4000)
            return "accepted"
        except MeetError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compete) for _ in range(2)]
        assert sorted(f.result(timeout=5) for f in futures) == ["accepted", "rejected"]
    assert row(store)["attempt"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": "../task"},
        {"assignment_digest": "synthetic-not-a-digest"},
        {"deadline_ms": True},
        {"deadline_ms": 0},
        {"deadline_ms": 2**53},
    ],
)
def test_owner_binding_is_closed_and_never_coerces_ambiguous_values(change):
    with pytest.raises(MeetError):
        replace(OWNER, **change)


@pytest.mark.parametrize(
    "change",
    [
        {"session_id": None},
        {"peer_id": "foreign"},
        {"generation": True},
        {"generation": 513},
        {"epoch": 0},
        {"epoch": 2**53},
        {"expires_ms": True},
        {"absolute_expires_ms": MEMBER.expires_ms - 1},
    ],
)
def test_membership_projection_has_strict_ids_and_bounded_integers(change):
    with pytest.raises(MeetError):
        replace(MEMBER, **change)


@pytest.mark.parametrize(
    "change",
    [
        {"generation": 2},
        {"expires_ms": MEMBER.expires_ms + 1},
        {"epoch": MEMBER.epoch - 1},
        {"absolute_expires_ms": MEMBER.absolute_expires_ms + 1},
    ],
)
def test_same_session_refresh_cannot_change_deadline_without_actual_renewal(store, change):
    with pytest.raises(MeetError, match="membership_changed"):
        store.observe(OWNER, replace(MEMBER, **change), NOW + 1)


def test_retired_first_session_or_peer_cannot_return_during_second_recovery(store):
    joining(store)
    next_member = replacement()
    store.observe(OWNER, next_member, NOW + 5000)
    joining(store, next_member, NOW + 6000, 2)
    for change in ({"session_id": MEMBER.session_id}, {"peer_id": MEMBER.peer_id}):
        with pytest.raises(MeetError, match="membership_changed"):
            store.observe(OWNER, replace(replacement("c", 7), **change), NOW + 11000)


@pytest.mark.parametrize(
    "patch",
    [
        {"attempt": -1},
        {"attempt": 3},
        {"retired": [{}]},
        {"membership": {}},
        {"state": "unknown"},
        {"attempt_until": 1},
        {"ready_at": 1},
        {"last_now": -1},
    ],
)
def test_corrupt_persisted_resource_cannot_reset_or_expand_recovery_budget(store, patch):
    with store.engine.begin() as connection:
        connection.execute(update(recoveries).values(**patch))
    with pytest.raises(MeetError, match="record_invalid"):
        store.reserve(OWNER, MEMBER.session_id, NOW + 1)


def _grant_process(url, barrier, outcomes):
    engine = create_engine(url, connect_args={"timeout": 5})
    try:
        repository = SqlDialogRecovery(engine)
        barrier.wait(timeout=10)
        try:
            repository.take_grant(OWNER, 1, MEMBER.session_id, NOW + 4000)
            result = "accepted"
        except MeetError:
            result = "rejected"
        outcomes.put((os.getpid(), result))
    finally:
        engine.dispose()


def test_actual_separate_hub_processes_cannot_claim_two_reconnect_grants(store, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    store.reserve(OWNER, MEMBER.session_id, NOW)
    store.retired(OWNER, 1, MEMBER.session_id, NOW)
    context = multiprocessing.get_context("spawn")
    barrier, outcomes = context.Barrier(2), context.Queue()
    children = [
        context.Process(target=_grant_process, args=(str(store.engine.url), barrier, outcomes)) for _ in range(2)
    ]
    try:
        for child in children:
            child.start()
        results = [outcomes.get(timeout=12) for _ in children]
        for child in children:
            child.join(timeout=2)
            assert child.exitcode == 0
        assert len({pid for pid, _ in results}) == 2 and os.getpid() not in {pid for pid, _ in results}
        assert sorted(result for _, result in results) == ["accepted", "rejected"]
        assert row(store)["state"] == "joining"
    finally:
        for child in children:
            if child.pid is not None and child.is_alive():
                child.kill()
                child.join(timeout=2)
            if child.pid is not None:
                assert not child.is_alive(), "owned reconnect probe cleanup failed"
                child.close()
        outcomes.close()
        outcomes.join_thread()
