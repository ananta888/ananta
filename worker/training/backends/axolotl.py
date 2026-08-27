from __future__ import annotations

from typing_extensions import Unpack

from worker.training.backends.external import (
    ExternalBackendDependencies,
    ExternalBackendSpec,
    ExternalCliTrainingBackend,
)


class AxolotlTrainingBackend(ExternalCliTrainingBackend):
    def __init__(self, **kwargs: Unpack[ExternalBackendDependencies]) -> None:
        super().__init__(
            ExternalBackendSpec(
                backend_id="axolotl",
                package_name="axolotl",
                version="0.18.0",
                executable="axolotl",
                license_spdx="Apache-2.0",
                maintenance="active",
                command_builder=lambda executable, config: (str(executable), "train", str(config)),
            ),
            **kwargs,
        )


__all__ = ["AxolotlTrainingBackend"]
