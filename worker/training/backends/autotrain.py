from __future__ import annotations

from typing_extensions import Unpack

from worker.training.backends.external import (
    ExternalBackendDependencies,
    ExternalBackendSpec,
    ExternalCliTrainingBackend,
)


class AutoTrainTrainingBackend(ExternalCliTrainingBackend):
    def __init__(self, **kwargs: Unpack[ExternalBackendDependencies]) -> None:
        super().__init__(
            ExternalBackendSpec(
                backend_id="autotrain",
                package_name="autotrain-advanced",
                version="0.8.36",
                executable="autotrain",
                license_spdx="Apache-2.0",
                maintenance="unmaintained",
                command_builder=lambda executable, config: (str(executable), "--config", str(config)),
            ),
            **kwargs,
        )


__all__ = ["AutoTrainTrainingBackend"]
