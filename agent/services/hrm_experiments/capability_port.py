"""SQL capability advertisement adapter for the Hub control-plane service."""

from __future__ import annotations

from agent.repositories.hrm_experiments import (
    HrmExperimentRepository,
    get_hrm_experiment_repository,
)
from agent.services.hrm_experiments.control_plane import HrmWorkerCapability


class SqlHrmWorkerCapabilityPort:
    def __init__(self, repository: HrmExperimentRepository | None = None) -> None:
        self._repository = repository or get_hrm_experiment_repository()

    def read_capability(self) -> HrmWorkerCapability | None:
        row = self._repository.current_capability()
        if row is None:
            return None
        projection = dict(row.projection or {})
        return HrmWorkerCapability(
            worker_id=str(projection.get("worker_id") or ""),
            runtime=dict(projection.get("runtime") or {}),
            device=dict(projection.get("device") or {}),
            isolation=dict(projection.get("isolation") or {}),
            supported_profiles=tuple(projection.get("supported_profiles") or ()),
        )


__all__ = ["SqlHrmWorkerCapabilityPort"]
