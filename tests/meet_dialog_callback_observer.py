"""Bounded code/timing-only Hub diagnostics; never records callback bodies or keys."""

import threading
import time
from collections import deque

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_service import MeetDialogService

FAILURES = frozenset(
    {
        "meet_authorization_unavailable",
        "meet_authorization_scope_invalid",
        "meet_authorization_state_invalid",
        "meet_authorization_contract_invalid",
        "meet_authorization_changed",
        "meet_dialog_lifecycle_unavailable",
        "meet_dialog_task_inactive",
        "meet_dialog_organization_inactive",
        "meet_dialog_policy_denied",
        "meet_dialog_expired",
        "meet_dialog_runtime_mismatch",
        "meet_dialog_source_profile_denied",
    }
)


class DialogCallbackObserver:
    def __init__(self, monkeypatch, *, clock=time.monotonic):
        self.lock = threading.Lock()
        self.exchanges = deque(maxlen=12)
        exchange = MeetDialogService.exchange

        def observe(service, payload):
            started = clock()
            record = {"status": "failed", "started_at_ms": round(started * 1000, 2)}
            try:
                result = exchange(service, payload)
                record.update(
                    status="ok",
                    generation=result["authorization"]["lease"]["generation"],
                    renewal=bool(result["renewal"]),
                )
                return result
            except MeetError as error:
                record.update(
                    code=error.code if error.code in FAILURES else "redacted",
                    http_status=error.status if type(error.status) is int and 400 <= error.status <= 599 else 0,
                )
                raise
            finally:
                record["elapsed_ms"] = round((clock() - started) * 1000, 2)
                with self.lock:
                    self.exchanges.append(record)

        monkeypatch.setattr(MeetDialogService, "exchange", observe)

    def report(self):
        with self.lock:
            return [dict(row) for row in self.exchanges]
