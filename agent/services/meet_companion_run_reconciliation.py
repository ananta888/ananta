"""Bounded Hub terminal-run recovery within the existing Meet lifecycle tick."""

from typing import Protocol


class CompanionTerminalTasksPort(Protocol):
    def terminal_tasks(self, after: str | None, limit: int) -> list: ...


class MeetCompanionRunReconciliation:
    def __init__(self, tasks: CompanionTerminalTasksPort, cleanup):
        self.tasks, self.cleanup, self.cursor = tasks, cleanup, None

    def run_once(self, *, limit=25, stopped=lambda: False):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("meet_companion_cleanup_limit_invalid")
        counts = {"scanned": 0, "settled": 0, "failed": 0}
        if stopped():
            return counts
        rows = self.tasks.terminal_tasks(self.cursor, limit)
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("meet_companion_cleanup_page_invalid")
        if not rows:
            self.cursor = None
        for task in rows:
            if stopped():
                break
            if not isinstance(task.id, str) or self.cursor is not None and task.id <= self.cursor:
                raise ValueError("meet_companion_cleanup_cursor_invalid")
            counts["scanned"] += 1
            try:
                context = (task.worker_execution_context or {}).get("meet_dialog", {})
                if not all(isinstance(context.get(key), str) and context[key] for key in ("lease_id", "runtime_id")):
                    raise ValueError("meet_companion_cleanup_binding_invalid")
                counts["settled"] += self.cleanup.terminal(task, lease_id=context["lease_id"],
                                                          runtime_id=context["runtime_id"])
            except Exception:  # noqa: BLE001 -- bounded diagnostic; next cursor cycle retries
                counts["failed"] += 1
            self.cursor = task.id
        return counts
