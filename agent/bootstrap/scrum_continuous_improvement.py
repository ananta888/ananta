"""Composition root for Hub-owned Scrum continuous-improvement services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.services.scrum_architecture_loop_service import ScrumArchitectureLoopService
from agent.services.scrum_continuous_improvement_query_service import (
    ScrumContinuousImprovementQueryService,
)
from agent.services.scrum_retrospective_service import (
    EvolutionRetrospectiveAnalysisAdapter,
    ScrumRetrospectiveService,
)
from agent.services.scrum_sprint_control_service import ScrumSprintControlService
from agent.services.scrum_state_store import ScrumStateStore


@dataclass(frozen=True, slots=True)
class ScrumContinuousImprovementWiringStatus:
    ready: bool
    reason_code: str | None


def initialize_scrum_continuous_improvement(app: Flask) -> ScrumContinuousImprovementWiringStatus:
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        status = ScrumContinuousImprovementWiringStatus(False, "scrum_improvement_hub_role_required")
    else:
        try:
            store = ScrumStateStore(
                Path(
                    str(
                        app.config.get("ANANTA_SCRUM_IMPROVEMENT_STATE")
                        or settings.scrum_improvement_state
                    )
                )
            )
            architecture = ScrumArchitectureLoopService(store)
            sprints = ScrumSprintControlService(store, architecture)
            core_services = app.extensions.get("core_services")
            evolution_service = getattr(core_services, "evolution_service", None)
            analysis = (
                EvolutionRetrospectiveAnalysisAdapter(evolution_service)
                if evolution_service is not None
                else None
            )
            retrospectives = ScrumRetrospectiveService(store, sprints, analysis=analysis)
            query = ScrumContinuousImprovementQueryService(store)
        except (OSError, RuntimeError, ValueError):
            status = ScrumContinuousImprovementWiringStatus(False, "scrum_improvement_configuration_invalid")
        else:
            app.extensions["scrum_state_store"] = store
            app.extensions["scrum_architecture_loop_service"] = architecture
            app.extensions["scrum_sprint_control_service"] = sprints
            app.extensions["scrum_retrospective_service"] = retrospectives
            app.extensions["scrum_continuous_improvement_query_service"] = query
            status = ScrumContinuousImprovementWiringStatus(True, None)
    app.extensions["scrum_continuous_improvement_wiring_status"] = status
    return status


__all__ = ["ScrumContinuousImprovementWiringStatus", "initialize_scrum_continuous_improvement"]
