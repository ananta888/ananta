"""Composition root for Hub-only automatic SIRA rollout state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.services.codecompass_sira_rollout_service import CodeCompassSiraRolloutService


@dataclass(frozen=True, slots=True)
class SiraRolloutWiringStatus:
    ready: bool
    reason_code: str | None


def initialize_codecompass_sira_rollout(app: Flask) -> SiraRolloutWiringStatus:
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        status = SiraRolloutWiringStatus(False, "sira_rollout_hub_role_required")
    else:
        try:
            service = CodeCompassSiraRolloutService(
                Path(
                    str(
                        app.config.get("CODECOMPASS_SIRA_ROLLOUT_STATE")
                        or settings.codecompass_sira_rollout_state
                        or "data/sira-rollout.sqlite3"
                    )
                )
            )
        except (OSError, RuntimeError, ValueError):
            status = SiraRolloutWiringStatus(False, "sira_rollout_configuration_invalid")
        else:
            app.extensions["codecompass_sira_rollout_service"] = service
            status = SiraRolloutWiringStatus(True, None)
    app.extensions["codecompass_sira_rollout_wiring_status"] = status
    return status


__all__ = ["SiraRolloutWiringStatus", "initialize_codecompass_sira_rollout"]
