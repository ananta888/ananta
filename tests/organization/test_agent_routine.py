"""PAO routine schedule: window, day vocabulary and once-per-period guard."""

from __future__ import annotations

import datetime as dt

import pytest

from ananta_contracts.agent_routine import RoutineSchedule, is_in_window, period_key, should_run

MONDAY = dt.datetime(2026, 10, 12, 7, 30)          # a Monday inside the window
SATURDAY = dt.datetime(2026, 10, 10, 7, 30)
MONTH_FIRST_MONDAY = dt.datetime(2026, 10, 5, 12, 0)
MONTH_LAST_FRIDAY = dt.datetime(2026, 10, 30, 17, 0)


def schedule(**changes) -> RoutineSchedule:
    base = dict(routine_id="web-standup", days="mon-fri", fire="07:30", window_start="07:15", window_end="11:30",
                key="YYYY-MM-DD", budget_minutes=15, browser="never")
    base.update(changes)
    return RoutineSchedule(**base)


def test_valid_schedule_round_trips():
    value = schedule()
    value.assert_valid()
    assert RoutineSchedule.from_mapping(value.to_mapping()) == value


@pytest.mark.parametrize("changes", [
    {"routine_id": "Bad Id"}, {"days": "daily"}, {"fire": "7:30"}, {"window_start": "99:00"},
    {"key": "YYYY"}, {"budget_minutes": 0}, {"browser": "sometimes"}, {"concurrency_policy": "maybe"},
    {"catch_up_policy": "always"}, {"window_start": "12:00", "window_end": "11:00"},
])
def test_invalid_schedule_is_rejected(changes):
    with pytest.raises(ValueError):
        schedule(**changes).assert_valid()


def test_period_keys_follow_the_declared_format():
    assert period_key(schedule(), dt.date(2026, 10, 12)) == "2026-10-12"
    assert period_key(schedule(key="YYYY-MM"), dt.date(2026, 10, 12)) == "2026-10"
    assert period_key(schedule(key="YYYY-Www"), dt.date(2026, 10, 12)) == "2026-W42"


def test_window_and_day_guard():
    assert is_in_window(schedule(), MONDAY) is True
    assert is_in_window(schedule(), dt.datetime(2026, 10, 12, 6, 0)) is False
    allowed, decision = should_run(schedule(), MONDAY, last_period_key=None)
    assert (allowed, decision) == (True, "run")
    assert should_run(schedule(), SATURDAY, last_period_key=None)[1] == "skipped-out-of-window"
    assert should_run(schedule(), MONDAY, last_period_key="2026-10-12")[1] == "skipped-already-ran"


def test_disabled_routine_never_runs():
    assert should_run(schedule(days="off"), MONDAY, last_period_key=None) == (False, "skipped-disabled")


def test_first_and_last_weekday_ranges():
    assert should_run(schedule(days="first-weekday", key="YYYY-MM", window_start="11:30", window_end="16:00"),
                      MONTH_FIRST_MONDAY, last_period_key=None)[0] is True
    assert should_run(schedule(days="first-weekday", key="YYYY-MM"), dt.datetime(2026, 10, 12, 12, 0),
                      last_period_key=None)[1] == "skipped-out-of-window"
    assert should_run(schedule(days="last-weekday", key="YYYY-MM", window_start="16:30", window_end="19:30"),
                      MONTH_LAST_FRIDAY, last_period_key=None)[0] is True


def test_skip_if_active_blocks_a_second_run():
    assert should_run(schedule(concurrency_policy="skip_if_active"), MONDAY, last_period_key=None,
                      active=True)[1] == "skipped-active-run"
