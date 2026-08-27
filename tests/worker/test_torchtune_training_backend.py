import pytest

from worker.training.backends.base import TrainingBackendError
from worker.training.backends.torchtune import TorchtuneTrainingBackend
from worker.training.torchtune_recipe_registry import require_recipe


def test_torchtune_is_default_experimental_with_fixed_recipe() -> None:
    backend = TorchtuneTrainingBackend(
        package_version=lambda _name: "0.6.1", executable_resolver=lambda _name: "/bin/true"
    )
    assert backend.capability().maintenance == "unmaintained"
    with pytest.raises(TrainingBackendError):
        require_recipe("attacker.module:Recipe")
