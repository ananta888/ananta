"""Service-layer composition boundary for the ML-Intern training repository."""

from __future__ import annotations

from agent.repositories.ml_intern_training import (
    MlInternTrainingRepositoryConflict,
)
from agent.repositories.ml_intern_training import (
    get_ml_intern_training_repository as _repository_factory,
)
from agent.services.ml_intern_training_repository_port import (
    MlInternTrainingRepositoryPort,
)

__all__ = [
    "MlInternTrainingRepositoryConflict",
    "get_ml_intern_training_repository",
]


def get_ml_intern_training_repository() -> MlInternTrainingRepositoryPort:
    """Return the Hub repository through its service-owned structural port."""

    return _repository_factory()
