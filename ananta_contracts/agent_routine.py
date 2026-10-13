"""Routine schedule contract with window and once-per-period guard (PAO, from ai-employees).

A routine is a declarative, schedulable unit of hub work. Its schedule row is the
single source of truth for ``days``, fire time, window, period key and budget; the
body never carries a clock time. The guard is pure: it decides whether a routine
may run now and which period key a run belongs to, so a missed or duplicated fire
never silently loses or repeats a period.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Mapping
from dataclasses import dataclass

ROUTINE_SCHEMA = "ananta.agent-routine-schedule.v1"
DAYS_VOCABULARY = ("mon-fri", "mon", "tue", "wed", "thu", "fri", "sat", "first-weekday", "last-weekday", "off")
_PERIOD_KEY_FORMATS = ("YYYY-MM-DD", "YYYY-Www", "YYYY-MM")
_BROWSER_LANES = ("never", "light", "conditional", "heavy")
_ROUTINE_ID = re.compile(r"[a-z][a-z0-9-]{1,63}")
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5}


@dataclass(frozen=True, slots=True)
class RoutineSchedule:
    routine_id: str
    days: str
    fire: str
    window_start: str
    window_end: str
    key: str
    budget_minutes: int
    browser: str = "never"
    concurrency_policy: str = "coalesce_if_active"
    catch_up_policy: str = "skip_missed"

    def assert_valid(self) -> None:
        if not isinstance(self.routine_id, str) or _ROUTINE_ID.fullmatch(self.routine_id) is None:
            raise ValueError("routine_id_invalid")
        if self.days not in DAYS_VOCABULARY:
            raise ValueError("routine_days_invalid")
        for name, value in (("fire", self.fire), ("window_start", self.window_start), ("window_end", self.window_end)):
            _parse_time(value, f"routine_{name}_invalid")
        if self.key not in _PERIOD_KEY_FORMATS:
            raise ValueError("routine_period_key_invalid")
        if type(self.budget_minutes) is not int or not 1 <= self.budget_minutes <= 24 * 60:
            raise ValueError("routine_budget_invalid")
        if self.browser not in _BROWSER_LANES:
            raise ValueError("routine_browser_invalid")
        if self.concurrency_policy not in {"coalesce_if_active", "skip_if_active", "allow"}:
            raise ValueError("routine_concurrency_policy_invalid")
        if self.catch_up_policy not in {"skip_missed", "run_once"}:
            raise ValueError("routine_catch_up_policy_invalid")
        if _parse_time(self.window_start, "") > _parse_time(self.window_end, ""):
            raise ValueError("routine_window_invalid")

    def to_mapping(self) -> dict:
        self.assert_valid()
        return {"schema": ROUTINE_SCHEMA, "routine_id": self.routine_id, "days": self.days, "fire": self.fire,
                "window_start": self.window_start, "window_end": self.window_end, "key": self.key,
                "budget_minutes": self.budget_minutes, "browser": self.browser,
                "concurrency_policy": self.concurrency_policy, "catch_up_policy": self.catch_up_policy}

    @classmethod
    def from_mapping(cls, payload: Mapping) -> "RoutineSchedule":
        if payload.get("schema") != ROUTINE_SCHEMA:
            raise ValueError("routine_schema_unsupported")
        schedule = cls(
            routine_id=payload.get("routine_id"), days=payload.get("days"), fire=payload.get("fire"),
            window_start=payload.get("window_start"), window_end=payload.get("window_end"),
            key=payload.get("key"), budget_minutes=payload.get("budget_minutes"),
            browser=payload.get("browser", "never"),
            concurrency_policy=payload.get("concurrency_policy", "coalesce_if_active"),
            catch_up_policy=payload.get("catch_up_policy", "skip_missed"),
        )
        schedule.assert_valid()
        return schedule


def _parse_time(value: str, reason: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", value):
        raise ValueError(reason or "routine_time_invalid")
    hour, minute = value.split(":")
    if int(hour) > 23:
        raise ValueError(reason or "routine_time_invalid")
    return int(hour) * 60 + int(minute)


def period_key(schedule: RoutineSchedule, local_date: _dt.date) -> str:
    """The once-per-period key for a run on ``local_date`` (local, never UTC)."""
    schedule.assert_valid()
    if schedule.key == "YYYY-MM-DD":
        return local_date.isoformat()
    if schedule.key == "YYYY-MM":
        return f"{local_date.year:04d}-{local_date.month:02d}"
    iso_year, iso_week, _ = local_date.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def is_in_window(schedule: RoutineSchedule, local_now: _dt.datetime) -> bool:
    schedule.assert_valid()
    return _parse_time(schedule.window_start, "") <= local_now.hour * 60 + local_now.minute <= _parse_time(
        schedule.window_end, "")


def is_allowed_day(schedule: RoutineSchedule, local_date: _dt.date) -> bool:
    schedule.assert_valid()
    if schedule.days == "off":
        return False
    weekday = local_date.weekday()
    if schedule.days == "mon-fri":
        return weekday <= 4
    if schedule.days in _WEEKDAYS:
        return weekday == _WEEKDAYS[schedule.days]
    if schedule.days == "first-weekday":
        return weekday <= 4 and local_date.day <= 7
    if schedule.days == "last-weekday":
        import calendar

        last_day = calendar.monthrange(local_date.year, local_date.month)[1]
        return weekday <= 4 and local_date.day > last_day - 7
    return False


def should_run(schedule: RoutineSchedule, local_now: _dt.datetime, *, last_period_key: str | None,
               active: bool = False) -> tuple[bool, str]:
    """Return ``(allowed, decision)``; ``decision`` is a stable, auditable reason code."""
    schedule.assert_valid()
    if schedule.days == "off":
        return False, "skipped-disabled"
    if not is_allowed_day(schedule, local_now.date()):
        return False, "skipped-out-of-window"
    if not is_in_window(schedule, local_now):
        return False, "skipped-out-of-window"
    if active and schedule.concurrency_policy == "skip_if_active":
        return False, "skipped-active-run"
    key = period_key(schedule, local_now.date())
    if last_period_key == key:
        return False, "skipped-already-ran"
    return True, "run"
