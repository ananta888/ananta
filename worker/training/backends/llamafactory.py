from __future__ import annotations

from typing_extensions import Unpack

from worker.training.backends.external import (
    ExternalBackendDependencies,
    ExternalBackendSpec,
    ExternalCliTrainingBackend,
)


class LlamaFactoryTrainingBackend(ExternalCliTrainingBackend):
    def __init__(self, **kwargs: Unpack[ExternalBackendDependencies]) -> None:
        super().__init__(
            ExternalBackendSpec(
                backend_id="llamafactory",
                package_name="llamafactory",
                version="0.9.5",
                executable="llamafactory-cli",
                license_spdx="Apache-2.0",
                maintenance="active",
                command_builder=lambda executable, config: (str(executable), "train", str(config)),
            ),
            **kwargs,
        )


__all__ = ["LlamaFactoryTrainingBackend"]
