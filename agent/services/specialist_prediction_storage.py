"""Confined, nonblocking read adapter; authorization belongs to the Hub consumer."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from agent.services.specialist_prediction_artifact import MAX_BYTES, SpecialistPredictionArtifactError


@contextmanager
def _open_confined(root: Path, relative: str):
    parts = PurePosixPath(relative)
    if parts.is_absolute() or not parts.parts or ".." in parts.parts:
        raise SpecialistPredictionArtifactError("specialist_prediction_path_invalid")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        child = os.open(parts.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(child, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 1 <= info.st_size <= MAX_BYTES:
                raise SpecialistPredictionArtifactError("specialist_prediction_file_invalid")
            yield handle
    finally:
        os.close(descriptor)


class ConfinedSpecialistPredictionStorage:
    def __init__(self, *, artifact_root: Path, dataset_root: Path):
        self._artifacts = Path(artifact_root).resolve(strict=True)
        self._datasets = Path(dataset_root).resolve(strict=True)

    def open_dataset(self, reference: str):
        try:
            relative = Path(reference).relative_to(self._datasets).as_posix()
        except ValueError as exc:
            raise SpecialistPredictionArtifactError("specialist_prediction_dataset_path_invalid") from exc
        return _open_confined(self._datasets, relative)

    def open_artifact(self, relative: str):
        return _open_confined(self._artifacts, relative)
