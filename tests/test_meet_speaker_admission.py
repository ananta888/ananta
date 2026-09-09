"""Headless policy and deadline checks over real SQL speaker-resource leases."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_speaker_floor import MeetSpeakerFloor
from tests import test_meet_speaker_floor as floor_tests
from tests.test_meet_speaker_floor import NOW, rows, turn

store = floor_tests.store

pytestmark = pytest.mark.timeout(30)


class Clock:
    def __init__(self):
        self.wall = NOW / 1000
        self.elapsed = 0

    def wait(self, duration):
        assert 0 < duration <= 0.1
        self.wall += duration
        self.elapsed += duration

    def service(self, store):
        return MeetSpeakerFloor(store, clock=lambda: self.wall, monotonic=lambda: self.elapsed, wait=self.wait)


def test_current_admitted_turn_receives_one_nonrenewable_permit(store):
    clock, current = Clock(), Mock()
    service = clock.service(store)
    permit = service.acquire(turn(), current)
    assert current.call_count >= 3
    service.require_current(turn(), permit, current)
    assert service.finish(turn(), permit)
    assert not service.finish(turn(), permit)
    assert rows(store)[0]["state"] == "quarantine"


def test_lost_current_authority_cancels_waiter_without_generating_or_publishing(store):
    clock = Clock()
    service = clock.service(store)
    service.acquire(turn(), lambda: None)
    current = Mock(side_effect=[None, None, MeetError("synthetic_revoked", 403)])
    with pytest.raises(MeetError, match="synthetic_revoked"):
        service.acquire(turn(2), current)
    assert rows(store)[0]["state"] == "active"
    assert rows(store)[1]["state"] == "finished"
    assert clock.elapsed <= 0.1


def test_post_grant_authority_change_quarantines_before_any_handoff(store):
    clock = Clock()
    current = Mock(side_effect=[None, None, MeetError("synthetic_revoked", 403)])
    with pytest.raises(MeetError, match="synthetic_revoked"):
        clock.service(store).acquire(turn(), current)
    assert rows(store)[0]["state"] == "quarantine"


def test_waiting_times_out_without_interactive_approval(store):
    clock = Clock()
    service = clock.service(store)
    service.acquire(turn(), lambda: None)
    with pytest.raises(MeetError, match="wait_expired|turn_inactive"):
        service.acquire(turn(2), lambda: None)
    assert 9.9 <= clock.elapsed <= 10.1
    assert rows(store)[0]["state"] == "active"
    assert rows(store)[1]["state"] in {"finished", "expired"}


def test_monotonic_wait_bound_survives_wall_clock_rollback(store):
    clock = Clock()
    service = clock.service(store)
    service.acquire(turn(), lambda: None)

    def backwards(duration):
        clock.elapsed += duration
        clock.wall -= duration

    service.wait = backwards
    with pytest.raises(MeetError, match="wait_expired"):
        service.acquire(turn(2), lambda: None)
    assert 9.9 <= clock.elapsed <= 10.1


def test_barge_in_requires_explicit_hub_policy_and_cannot_restore_old_turn(store):
    clock = Clock()
    service = clock.service(store)
    permit = service.acquire(turn(), lambda: None)
    denied = Mock(side_effect=MeetError("synthetic_policy_denied", 403))
    with pytest.raises(MeetError, match="policy_denied"):
        service.interrupt(turn(), permit, denied)
    assert store.current(turn(), permit, NOW)
    allowed = Mock()
    assert service.interrupt(turn(), permit, allowed)
    assert allowed.call_count == 2
    assert not store.current(turn(), permit, NOW)
    assert not service.interrupt(turn(), permit, allowed)
    with pytest.raises(MeetError, match="duplicate_turn"):
        service.acquire(turn(), lambda: None)


def test_recheck_after_database_read_catches_policy_withdrawal(store):
    clock = Clock()
    service = clock.service(store)
    permit = service.acquire(turn(), lambda: None)
    changed = Mock(side_effect=[None, MeetError("synthetic_revoked", 403)])
    with pytest.raises(MeetError, match="synthetic_revoked"):
        service.require_current(turn(), permit, changed)
    assert rows(store)[0]["state"] == "quarantine"


def test_rejected_initial_authority_creates_no_reservation(store):
    denied = Mock(side_effect=MeetError("synthetic_revoked", 403))
    with pytest.raises(MeetError, match="synthetic_revoked"):
        Clock().service(store).acquire(turn(), denied)
    assert rows(store) == []


def test_automatic_higher_priority_admission_is_explicit_and_waits_through_quarantine(store):
    clock = Clock()
    service = clock.service(store)
    first, next_turn = replace(turn(), organization_id="org"), replace(turn(2, priority=2), organization_id="org")
    old = service.acquire(first, lambda: None)
    permit = service.acquire(next_turn, lambda: None, interrupt=True)
    assert permit["sequence"] == 2
    assert 4 <= clock.elapsed < 4.2
    assert not store.current(first, old, int(clock.wall * 1000))


def test_high_priority_without_explicit_interruption_policy_cannot_preempt(store):
    clock = Clock()
    service = clock.service(store)
    first, next_turn = replace(turn(), organization_id="org"), replace(turn(2, priority=2), organization_id="org")
    old = service.acquire(first, lambda: None)
    with pytest.raises(MeetError, match="wait_expired|turn_inactive"):
        service.acquire(next_turn, lambda: None)
    assert store.current(first, old, int(clock.wall * 1000))
