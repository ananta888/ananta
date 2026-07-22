"""Production-only composition for persistent TURN accounting."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from flask import Flask

from agent.database import engine as default_engine
from agent.repositories.turn_accounting_repository import SqlTurnAccountingRepository
from agent.services.turn_accounting_service import TurnAccountingError, TurnAccountingService


@dataclass(frozen=True, slots=True)
class TurnAccountingWiringStatus:
    ready: bool
    reason_code: str | None


def initialize_turn_accounting(
    app: Flask,
    *,
    db_engine=default_engine,
) -> TurnAccountingWiringStatus:
    secret = str(app.secret_key or "")
    if not secret:
        app.extensions.pop("turn_accounting_repository", None)
        app.extensions.pop("turn_accounting_service", None)
        status = TurnAccountingWiringStatus(False, "turn_accounting_secret_unavailable")
        app.extensions["turn_accounting_status"] = status
        return status
    try:
        repository = SqlTurnAccountingRepository(
            db_engine=db_engine,
            purge_batch=_integer_setting("ANANTA_TURN_ACCOUNTING_PURGE_BATCH", 200),
        )
        service = TurnAccountingService(
            repository,
            pseudonym_secret=hashlib.sha256(
                b"ananta:turn-accounting:v1\0" + secret.encode("utf-8")
            ).digest(),
            window_seconds=_integer_setting("ANANTA_TURN_ACCOUNTING_WINDOW_SECONDS", 60),
            retention_seconds=_integer_setting(
                "ANANTA_TURN_ACCOUNTING_RETENTION_SECONDS", 86_400
            ),
            late_window_seconds=_integer_setting(
                "ANANTA_TURN_ACCOUNTING_LATE_WINDOW_SECONDS", 300
            ),
            max_sources=_integer_setting("ANANTA_TURN_ACCOUNTING_MAX_SOURCES", 4096),
            max_records=_integer_setting("ANANTA_TURN_ACCOUNTING_MAX_RECORDS", 262_144),
            control_observer=app.extensions.get("sfu_broadcast_control_observer"),
        )
    except (ValueError, TurnAccountingError):
        app.extensions.pop("turn_accounting_repository", None)
        app.extensions.pop("turn_accounting_service", None)
        status = TurnAccountingWiringStatus(False, "turn_accounting_configuration_invalid")
        app.extensions["turn_accounting_status"] = status
        return status
    app.extensions["turn_accounting_repository"] = repository
    app.extensions["turn_accounting_service"] = service
    status = TurnAccountingWiringStatus(True, None)
    app.extensions["turn_accounting_status"] = status
    return status


def _integer_setting(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("turn_accounting_configuration_invalid") from exc
    if str(parsed) != value.strip():
        raise ValueError("turn_accounting_configuration_invalid")
    return parsed


__all__ = ["TurnAccountingWiringStatus", "initialize_turn_accounting"]
