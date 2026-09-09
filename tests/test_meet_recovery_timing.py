"""Native returns, exceptions and payloads never become timing authority."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_recovery_timing import RecoveryTiming


def test_timing_retains_only_bounded_copied_numbers_and_fixed_tags():
    clock = [100.0]
    timing = RecoveryTiming(clock=lambda: clock[0])
    result = object()

    def native(secret):
        assert secret == "private"
        clock[0] += 0.03
        return result

    observed = timing.wrap(native, "storage")
    for _ in range(40):
        assert observed("private") is result
    assert len(timing.report()) == 32
    assert all(
        set(row) == {"port", "started_ms", "elapsed_ms"} and row["port"] == "storage" and row["elapsed_ms"] == 30
        for row in timing.report()
    )
    projection = timing.report()
    projection[0]["port"] = "mutated"
    assert timing.report()[0]["port"] == "storage" and "private" not in str(timing.report())


def test_fast_call_is_not_recorded_and_native_failure_is_not_replaced():
    clock = [100.0]
    timing = RecoveryTiming(clock=lambda: clock[0])
    failure = ValueError("private")
    with pytest.raises(ValueError) as caught:
        timing.wrap(Mock(side_effect=failure), "authority")()
    assert caught.value is failure and timing.report() == []


def test_only_six_named_native_ports_are_wrapped(monkeypatch):
    authority, meet, recovery, phases, service = [SimpleNamespace() for _ in range(5)]
    authority.current = meet._inspect = recovery.observe = phases.advance = service.exchange = Mock()
    recovery.states = SimpleNamespace(observe=Mock())
    before = authority.current
    timing = RecoveryTiming()
    timing.install(monkeypatch, authority, meet, recovery, phases, service)
    authority.current("private")
    before.assert_called_once_with("private")
    assert timing.report() == []


def test_authority_detail_has_an_independent_bounded_history():
    clock = [100.0]
    timing = RecoveryTiming(clock=lambda: clock[0])

    def native():
        clock[0] += 0.03

    timing.wrap(native, "exchange")()
    for _ in range(40):
        timing.wrap(native, "task-read", detailed=True)()
    rows = timing.report()
    assert len(rows) == 33 and rows[0]["port"] == "exchange"
    assert all(row["port"] == "task-read" for row in rows[1:])
