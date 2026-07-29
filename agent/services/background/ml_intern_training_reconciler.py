"""Lifecycle-owned Hub thread for durable LoRA training recovery."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Mapping

from agent.config import settings
from agent.services.ml_intern_training_reconciliation_service import (
    MlInternTrainingReconciliationService,
    build_ml_intern_training_reconciliation_service,
)
from agent.services.unsloth_cleanup_reservation_reconciler import (
    UnslothCleanupReservationPolicy,
    UnslothCleanupReservationReconciler,
)
from agent.services.unsloth_storage_governance_service import (
    storage_catalog_from_config,
)
from agent.services.unsloth_task_port import (
    HubUnslothTaskSubmissionAdapter,
)

_EXTENSION_KEY = "ml_intern_training_reconciler"


def start_ml_intern_training_reconciler_thread(app) -> None:
    """Start one Hub-only bounded recovery loop owned by app lifecycle."""

    if settings.role != "hub":
        return
    extensions = getattr(app, "extensions", None)
    if isinstance(extensions, dict):
        existing = extensions.get(_EXTENSION_KEY)
        if isinstance(existing, Mapping):
            thread = existing.get("thread")
            if isinstance(thread, threading.Thread) and thread.is_alive():
                return

    config = _training_config(app)
    service = build_ml_intern_training_reconciliation_service(config)
    cleanup_reservations = UnslothCleanupReservationReconciler(
        tasks=HubUnslothTaskSubmissionAdapter(),
        catalog=storage_catalog_from_config(config),
        policy=UnslothCleanupReservationPolicy.from_mapping(config),
        audit=_audit,
        is_hub=lambda: settings.role == "hub",
    )
    interval = _interval_seconds(config)

    def run() -> None:
        import agent.common.context

        try:
            while not agent.common.context.shutdown_requested:
                try:
                    with app.app_context():
                        result = service.run_once()
                    if result["reconciled"] or result["conflicts"] or result["errors"]:
                        logging.info(
                            "ML training reconciliation: scanned=%s reconciled=%s "
                            "retried=%s cancelled=%s failed=%s conflicts=%s",
                            result["scanned"],
                            result["reconciled"],
                            result["retried"],
                            result["cancelled"],
                            result["failed"],
                            result["conflicts"],
                        )
                except Exception as exc:  # next bounded tick retries persistent state
                    logging.warning(
                        "ML training reconciliation unavailable: %s",
                        type(exc).__name__,
                    )
                try:
                    with app.app_context():
                        cleanup_result = cleanup_reservations.run_once()
                    if (
                        cleanup_result["activated"]
                        or cleanup_result["rejected"]
                        or cleanup_result["conflicts"]
                        or cleanup_result["invalid"]
                        or cleanup_result["errors"]
                    ):
                        logging.info(
                            "Unsloth cleanup reservation reconciliation: "
                            "scanned=%s activated=%s rejected=%s deferred=%s "
                            "conflicts=%s invalid=%s",
                            cleanup_result["scanned"],
                            cleanup_result["activated"],
                            cleanup_result["rejected"],
                            cleanup_result["deferred"],
                            cleanup_result["conflicts"],
                            cleanup_result["invalid"],
                        )
                except Exception as exc:
                    logging.warning(
                        "Unsloth cleanup reservation reconciliation unavailable: %s",
                        type(exc).__name__,
                    )
                if not _wait(interval):
                    break
        finally:
            service.begin_shutdown()

    thread = threading.Thread(
        target=run,
        name="ml-intern-training-reconciler",
        daemon=True,
    )
    if isinstance(extensions, dict):
        extensions[_EXTENSION_KEY] = {
            "service": service,
            "cleanup_reservation_service": cleanup_reservations,
            "thread": thread,
        }
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def stop_ml_intern_training_reconciler(app) -> None:
    """Enter drain mode immediately; the lifecycle then joins the thread."""

    extensions = getattr(app, "extensions", None)
    state = extensions.get(_EXTENSION_KEY) if isinstance(extensions, dict) else None
    service = state.get("service") if isinstance(state, Mapping) else None
    if isinstance(service, MlInternTrainingReconciliationService):
        service.begin_shutdown()
        from agent.services.ml_intern_training_control_service import (
            begin_ml_intern_training_control_shutdown,
        )

        begin_ml_intern_training_control_shutdown()


def _training_config(app) -> dict[str, Any]:
    app_config = getattr(app, "config", {}) or {}
    agent_config = app_config.get("AGENT_CONFIG") or {}
    raw_training = agent_config.get("ml_intern_training")
    config = dict(raw_training) if isinstance(raw_training, Mapping) else {}
    raw_runtime = agent_config.get("lora_runtime")
    config["lora_runtime"] = dict(raw_runtime) if isinstance(raw_runtime, Mapping) else {}
    return config


def _interval_seconds(config: Mapping[str, Any]) -> float:
    reconciliation = config.get("reconciliation")
    nested = reconciliation if isinstance(reconciliation, Mapping) else {}
    value = os.environ.get("ANANTA_ML_TRAINING_RECONCILE_INTERVAL_SECONDS")
    if value is None:
        value = nested.get("interval_seconds", 5.0)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 5.0
    return max(0.25, min(parsed, 300.0))


def _wait(seconds: float) -> bool:
    import agent.common.context

    deadline = time.monotonic() + seconds
    while not agent.common.context.shutdown_requested:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.25, remaining))
    return False


def _audit(action: str, details: dict[str, Any]) -> None:
    from agent.common.audit import log_audit

    log_audit(action, details)


__all__ = [
    "start_ml_intern_training_reconciler_thread",
    "stop_ml_intern_training_reconciler",
]
