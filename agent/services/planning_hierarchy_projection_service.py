"""Canonical read-model entry point for the two-level planning hierarchy."""

from agent.services.planning_status_projection_service import (
    PlanningStatusProjectionService,
)


class PlanningHierarchyProjectionService(PlanningStatusProjectionService):
    """Named facade retained for API/UI composition without extra writes."""


__all__ = ["PlanningHierarchyProjectionService", "PlanningStatusProjectionService"]
