"""Bounded code/timing-only Hub diagnostics; never records callback bodies or keys."""

import threading
import time
from collections import deque

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_service import MeetDialogService


class DialogCallbackObserver:
    def __init__(self, monkeypatch):
        self.lock = threading.Lock()
        self.exchanges = deque(maxlen=12)
        exchange = MeetDialogService.exchange

        def observe(service, payload):
            started = time.monotonic()
            record = {"status": "failed"}
            try:
                result = exchange(service, payload)
                record.update(
                    status="ok",
                    generation=result["authorization"]["lease"]["generation"],
                    renewal=bool(result["renewal"]),
                )
                return result
            except MeetError as error:
                record.update(code=error.code, http_status=error.status)
                raise
            finally:
                record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
                with self.lock:
                    self.exchanges.append(record)

        monkeypatch.setattr(MeetDialogService, "exchange", observe)

    def report(self):
        with self.lock:
            return list(self.exchanges)
