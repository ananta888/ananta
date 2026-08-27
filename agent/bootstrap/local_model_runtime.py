"""Composition root for the optional Hub-owned KAT/LFM/Needle runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass

from flask import Flask

from agent.services.local_model_runtime_composition import (
    get_local_model_runtime_composition,
)


@dataclass(frozen=True, slots=True)
class LocalModelRuntimeWiringStatus:
    ready: bool
    reason_code: str | None


def initialize_local_model_runtime_services(app: Flask) -> LocalModelRuntimeWiringStatus:
    role = str(app.config.get("ROLE") or "").strip().lower()
    if role != "hub":
        status = LocalModelRuntimeWiringStatus(False, "local_runtime_hub_role_required")
        app.extensions["local_model_runtime_wiring_status"] = status
        return status
    if not _enabled(app):
        status = LocalModelRuntimeWiringStatus(False, "local_runtime_disabled")
        app.extensions["local_model_runtime_wiring_status"] = status
        return status
    try:
        get_local_model_runtime_composition(app)
    except (OSError, RuntimeError, ValueError):
        app.extensions.pop("local_model_runtime_composition", None)
        app.extensions.pop("model_invocation_observation_port", None)
        app.extensions.pop("tiny_router_telemetry_sink", None)
        status = LocalModelRuntimeWiringStatus(False, "local_runtime_configuration_invalid")
    else:
        status = LocalModelRuntimeWiringStatus(True, None)
    app.extensions["local_model_runtime_wiring_status"] = status
    return status


def _enabled(app: Flask) -> bool:
    configured = app.config.get("ANANTA_LOCAL_MODEL_RUNTIME_ENABLED")
    if isinstance(configured, bool):
        return configured
    value = (
        str(configured if configured is not None else os.environ.get("ANANTA_LOCAL_MODEL_RUNTIME_ENABLED", "0"))
        .strip()
        .lower()
    )
    return value in {"1", "true", "yes", "on"}


__all__ = ["LocalModelRuntimeWiringStatus", "initialize_local_model_runtime_services"]
