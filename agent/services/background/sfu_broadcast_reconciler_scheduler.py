"""Flask lifecycle adapter for the Hub-owned SFU scheduler."""

from __future__ import annotations

from agent.services.sfu_broadcast_reconciler_scheduler import SfuBroadcastReconcilerScheduler


def start_sfu_broadcast_reconciler_scheduler(app) -> None:
    scheduler = app.extensions.get("sfu_broadcast_reconciler_scheduler")
    if isinstance(scheduler, SfuBroadcastReconcilerScheduler):
        scheduler.start()


def stop_sfu_broadcast_reconciler_scheduler(app) -> None:
    scheduler = app.extensions.get("sfu_broadcast_reconciler_scheduler")
    if isinstance(scheduler, SfuBroadcastReconcilerScheduler):
        scheduler.stop()


__all__ = ["start_sfu_broadcast_reconciler_scheduler", "stop_sfu_broadcast_reconciler_scheduler"]
