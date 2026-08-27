from __future__ import annotations

from typing_extensions import Unpack

from worker.training.backends.external import (
    ExternalBackendDependencies,
    ExternalBackendSpec,
    ExternalCliTrainingBackend,
)
from worker.training.torchtune_recipe_registry import require_recipe


class TorchtuneTrainingBackend(ExternalCliTrainingBackend):
    def __init__(self, **kwargs: Unpack[ExternalBackendDependencies]) -> None:
        recipe = require_recipe("llama3_2_3b_lora_single_device")
        super().__init__(
            ExternalBackendSpec(
                backend_id="torchtune",
                package_name="torchtune",
                version="0.6.1",
                executable="tune",
                license_spdx="BSD-3-Clause",
                maintenance="unmaintained",
                command_builder=lambda executable, config: (
                    str(executable),
                    "run",
                    recipe,
                    "--config",
                    str(config),
                ),
            ),
            **kwargs,
        )


__all__ = ["TorchtuneTrainingBackend"]
