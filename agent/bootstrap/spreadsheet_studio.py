"""Hub-only composition for the default-off spreadsheet studio mock slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.adapters.spreadsheet_mock_execution_adapter import (
    DeterministicSpreadsheetMockExecutionAdapter,
)
from agent.config import settings
from agent.services.spreadsheet_artifact_store import SpreadsheetArtifactStore
from agent.services.spreadsheet_learning_service import SpreadsheetLearningService
from agent.services.spreadsheet_learning_store import SpreadsheetLearningStore
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService
from agent.services.spreadsheet_store import SpreadsheetStore
from agent.services.spreadsheet_worker_port import spreadsheet_worker_port_from_environment


@dataclass(frozen=True, slots=True)
class SpreadsheetStudioWiringStatus:
    ready: bool
    reason_code: str | None


def initialize_spreadsheet_studio(app: Flask) -> SpreadsheetStudioWiringStatus:
    enabled = _bool(app.config.get("ANANTA_SPREADSHEET_STUDIO_ENABLED", settings.spreadsheet_studio_enabled))
    mode = str(app.config.get("ANANTA_SPREADSHEET_STUDIO_MODE", settings.spreadsheet_studio_mode)).strip()
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        status = SpreadsheetStudioWiringStatus(False, "spreadsheet_hub_role_required")
    else:
        policy = SpreadsheetPolicy(
            enabled=enabled,
            mode=mode,
            automatic_promotion_enabled=_bool(
                app.config.get(
                    "ANANTA_SPREADSHEET_STUDIO_AUTOMATIC_PROMOTION_ENABLED",
                    settings.spreadsheet_studio_automatic_promotion_enabled,
                )
            ),
        )
        try:
            policy.validate()
        except ValueError:
            status = SpreadsheetStudioWiringStatus(False, "spreadsheet_configuration_invalid")
        else:
            if enabled:
                try:
                    state = Path(
                        str(app.config.get("ANANTA_SPREADSHEET_STUDIO_STATE") or settings.spreadsheet_studio_state)
                    )
                    executor = (
                        DeterministicSpreadsheetMockExecutionAdapter()
                        if mode == "mock"
                        else spreadsheet_worker_port_from_environment()
                    )
                    document_store = SpreadsheetStore(state)
                    app.extensions["spreadsheet_studio_service"] = SpreadsheetSagaService(
                        document_store,
                        policy=policy,
                        executor=executor,
                        artifact_store=SpreadsheetArtifactStore(state.parent / "spreadsheet-artifacts"),
                    )
                    app.extensions["spreadsheet_learning_service"] = SpreadsheetLearningService(
                        documents=document_store,
                        store=SpreadsheetLearningStore(state),
                        dataset_root=state.parent / "spreadsheet-datasets",
                    )
                except (RuntimeError, ValueError):
                    status = SpreadsheetStudioWiringStatus(False, "spreadsheet_worker_configuration_invalid")
                else:
                    status = SpreadsheetStudioWiringStatus(True, None)
            else:
                status = SpreadsheetStudioWiringStatus(False, "spreadsheet_studio_disabled")
    app.extensions["spreadsheet_studio_wiring_status"] = status
    return status


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["SpreadsheetStudioWiringStatus", "initialize_spreadsheet_studio"]
