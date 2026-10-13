"""Read-only storage boundary for Hub specialist evaluation consumption."""

from typing import BinaryIO, ContextManager, Protocol


class SpecialistPredictionStoragePort(Protocol):
    def open_dataset(self, reference: str) -> ContextManager[BinaryIO]: ...
    def open_artifact(self, relative: str) -> ContextManager[BinaryIO]: ...
