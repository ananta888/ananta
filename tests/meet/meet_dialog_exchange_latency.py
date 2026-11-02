"""Explicit CPU-only latency injection; delegates every decision to the real Hub."""

import time

from agent.services.meet_dialog_service import MeetDialogService


def inject_exchange_latency(monkeypatch):
    exchange = MeetDialogService.exchange

    def delayed(service, payload):
        time.sleep(0.3)
        return exchange(service, payload)

    monkeypatch.setattr(MeetDialogService, "exchange", delayed)
