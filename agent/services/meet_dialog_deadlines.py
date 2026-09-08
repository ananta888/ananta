"""Bounded Hub cleanup of original deadlines; no grants, retries or dispatch."""

import math
import time
from typing import Callable, Protocol

KINDS = {
    "meet_dialog_session": "meet_dialog",
    "meet_audio_receive": "meet_audio",
    "meet_browser_workspace": "meet_browser_job",
}


class DialogDeadlineStore(Protocol):
    def page(self, after: str | None, limit: int) -> list[dict]: ...
    def settle(self, candidate: dict, still_expired: Callable[[], bool]) -> bool: ...


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 191


def original_deadline(candidate):
    """Inspect only a known task's captured identity; missing fields are not defaults."""
    if (
        not isinstance(candidate, dict)
        or set(candidate) != {"task_id", "task_kind", "tenant_id", "project_id", "parent_task_id", "context"}
        or not all(_identifier(candidate[key]) for key in ("task_id", "tenant_id", "project_id"))
        or not isinstance(candidate["task_kind"], str)
        or candidate["task_kind"] not in KINDS
        or not isinstance(candidate["context"], dict)
    ):
        raise ValueError("meet_deadline_task_invalid")
    context = candidate["context"]
    binding = context.get(KINDS[candidate["task_kind"]])
    if candidate["task_kind"] == "meet_browser_workspace":
        from ananta_contracts.meet_browser_workspace import validate_browser_job

        job = validate_browser_job(binding)
        if set(context) != {"meet_browser_job", "parent_dispatch", "runtime_id"} or (
            job["task_id"],
            job["parent_task_id"],
            job["tenant_id"],
            job["project_id"],
            job["parent_lease_id"],
            job["runtime_id"],
        ) != (
            candidate["task_id"],
            candidate["parent_task_id"],
            candidate["tenant_id"],
            candidate["project_id"],
            context["parent_dispatch"],
            context["runtime_id"],
        ):
            raise ValueError("meet_deadline_binding_invalid")
        return job["deadline_ms"] / 1000
    if (
        not isinstance(binding, dict)
        or not _identifier(binding.get("lease_id"))
        or type(binding.get("deadline")) is not int
        or not 0 < binding["deadline"] < 2**53
    ):
        raise ValueError("meet_deadline_binding_invalid")
    if candidate["task_kind"] == "meet_dialog_session":
        valid = _identifier(binding.get("runtime_id"))
    else:
        valid = (
            _identifier(candidate["parent_task_id"])
            and binding.get("task_id") == candidate["task_id"]
            and _identifier(context.get("parent_dispatch"))
            and _identifier(context.get("runtime_id"))
        )
    if not valid:
        raise ValueError("meet_deadline_binding_invalid")
    return binding["deadline"]


class MeetDialogDeadlines:
    def __init__(self, store: DialogDeadlineStore, *, clock=time.time):
        self.store, self.clock = store, clock
        self.cursor = None

    def _now(self):
        now = self.clock()
        if type(now) not in {int, float} or not 0 < now < 2**53 or not math.isfinite(now):
            raise ValueError("meet_deadline_clock_invalid")
        return now

    def run_once(self, *, limit=25, stopped=lambda: False):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("meet_deadline_limit_invalid")
        counts = {"scanned": 0, "settled": 0, "live": 0, "invalid": 0, "conflict": 0}
        self._now()
        if stopped():
            return counts
        rows = self.store.page(self.cursor, limit)
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("meet_deadline_page_invalid")
        if not rows:
            self.cursor = None
        for candidate in rows:
            if stopped():
                break
            key = candidate.get("task_id") if isinstance(candidate, dict) else None
            # A malformed persisted identifier is still an ordering key, never
            # authority: count/reject it below and let later valid rows progress.
            if not isinstance(key, str) or self.cursor is not None and key <= self.cursor:
                raise ValueError("meet_deadline_cursor_invalid")
            counts["scanned"] += 1
            try:
                deadline = original_deadline(candidate)
            except ValueError:
                counts["invalid"] += 1
            else:
                if deadline > self._now():
                    counts["live"] += 1
                else:
                    # Recheck the actual clock and shutdown inside the existing
                    # authoritative task CAS, including after a blocked DB read.
                    settled = self.store.settle(candidate, lambda: not stopped() and deadline <= self._now())
                    counts["settled" if settled else "conflict"] += 1
            self.cursor = key
        return counts
