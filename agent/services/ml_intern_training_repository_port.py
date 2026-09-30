"""Compatibility re-export of the ML-Intern training repository port.

The principal value object and the structural repository port are
dependency-free contracts and live in
:mod:`agent.ports.ml_intern_training_repository`, so the repository adapter can
implement them without importing the service layer.
"""

from __future__ import annotations

from agent.ports.ml_intern_training_repository import (
    MlInternTrainingPrincipal,
    MlInternTrainingRepositoryPort,
)

__all__ = ["MlInternTrainingPrincipal", "MlInternTrainingRepositoryPort"]
