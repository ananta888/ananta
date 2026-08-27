from __future__ import annotations

from dataclasses import dataclass

import pytest

from worker.training.backend_registry import FrozenTrainingBackendRegistry
from worker.training.backends.base import TrainingBackendError


@dataclass
class _Backend:
    name: str
    available: bool = True

    def availability(self) -> tuple[bool, str | None]:
        return self.available, None if self.available else "missing"


def test_registry_is_startup_frozen_and_projects_partial_availability() -> None:
    source = [_Backend("mock"), _Backend("needle", available=False)]
    registry = FrozenTrainingBackendRegistry(source)  # type: ignore[arg-type]
    source.append(_Backend("peft_trl"))
    assert list(registry) == ["mock", "needle"]
    assert registry.capabilities()["mock"]["available"] is True
    assert registry.capabilities()["needle"]["reason_code"] == "dependency_unavailable"
    with pytest.raises(TypeError):
        registry._backends["late"] = source[-1]  # type: ignore[index]


def test_registry_rejects_duplicates_and_unknown_backends() -> None:
    with pytest.raises(ValueError):
        FrozenTrainingBackendRegistry([_Backend("mock"), _Backend("mock")])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="allowlisted"):
        FrozenTrainingBackendRegistry([_Backend("request_module")])  # type: ignore[list-item]
    registry = FrozenTrainingBackendRegistry([])
    with pytest.raises(TrainingBackendError) as error:
        registry.require("missing")
    assert error.value.code == "backend_unavailable"
