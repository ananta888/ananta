"""Lifecycle-owned Hub thread for workflow runtime reconciliation."""

from __future__ import annotations

import logging
import os
import threading
import time

from agent.config import settings


def start_workflow_runtime_reconciler_thread(app) -> None:
    if settings.role != "hub":
        return

    interval = _interval_seconds()

    def run() -> None:
        import agent.common.context

        while not agent.common.context.shutdown_requested:
            try:
                with app.app_context():
                    from agent.services.workflow_runtime_reconciler_service import (
                        build_workflow_runtime_reconciler_service,
                    )

                    result = build_workflow_runtime_reconciler_service().run_once()
                if result["processed"] or result["failed"]:
                    logging.info(
                        "Workflow runtime reconciliation: runtime=%s processed=%s failed=%s",
                        result.get("runtime_id")
                        or ",".join(result.get("runtime_ids") or ()),
                        result["processed"],
                        len(result["failed"]),
                    )
            except Exception as exc:  # service retries; it never executes worker work
                logging.warning(
                    "Workflow runtime reconciliation unavailable: %s",
                    type(exc).__name__,
                )
            if not _wait(interval):
                break

    thread = threading.Thread(
        target=run,
        name="workflow-runtime-reconciler",
        daemon=True,
    )
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def _interval_seconds() -> float:
    try:
        value = float(
            os.environ.get("ANANTA_WORKFLOW_RECONCILE_INTERVAL_SECONDS") or 1.0
        )
    except (TypeError, ValueError):
        value = 1.0
    return max(0.25, min(value, 60.0))


def _wait(seconds: float) -> bool:
    import agent.common.context

    deadline = time.monotonic() + seconds
    while not agent.common.context.shutdown_requested:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.25, remaining))
    return False


__all__ = ["start_workflow_runtime_reconciler_thread"]
