"""Bounded, content-free timing of fixed native recovery service boundaries."""

import threading
import time
from collections import deque


class RecoveryTiming:
    def __init__(self, *, clock=time.monotonic):
        self.clock, self.lock = clock, threading.Lock()
        self.rows = deque(maxlen=32)
        self.authority_rows = deque(maxlen=32)

    def install(self, patcher, authority, meet, recovery, phases, service):
        for owner, method, tag in (
            (authority, "current", "authority"),
            (meet, "_inspect", "meet"),
            (recovery, "observe", "recovery"),
            (recovery.states, "observe", "storage"),
            (phases, "advance", "phase"),
            (service, "exchange", "exchange"),
        ):
            patcher.setattr(owner, method, self.wrap(getattr(owner, method), tag))

    def install_authority(self, patcher, authority):
        from agent.repositories.meet_role_assignment import SqlMeetRoleAssignments
        from agent.services.meet_dialog_lifecycle import MeetDialogLifecycle
        from agent.services.meet_organization_topology import MeetOrganizationTopologyGate

        for owner, method, tag in (
            (authority.tasks, "get_by_id", "task-read"),
            (MeetDialogLifecycle, "require_current", "lifecycle"),
            (MeetOrganizationTopologyGate, "evaluate", "topology"),
            (SqlMeetRoleAssignments, "read", "role-read"),
        ):
            patcher.setattr(owner, method, self.wrap(getattr(owner, method), tag, detailed=True))

    def wrap(self, native, tag, *, detailed=False):
        def observed(*args, **kwargs):
            started = self.clock()
            try:
                return native(*args, **kwargs)
            finally:
                elapsed = (self.clock() - started) * 1000
                if elapsed >= 20:
                    with self.lock:
                        rows = self.authority_rows if detailed else self.rows
                        rows.append(
                            {"port": tag, "started_ms": round(started * 1000, 2), "elapsed_ms": round(elapsed, 2)}
                        )

        return observed

    def report(self):
        with self.lock:
            return [dict(row) for row in (*self.rows, *self.authority_rows)]
