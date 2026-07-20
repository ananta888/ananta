"""Bounded checkpoint verification for one fenced speech training attempt."""

from __future__ import annotations

import hashlib
from pathlib import Path

from worker.speech_training.backend import SpeechCheckpoint, SpeechTrainingBackendError
from worker.speech_training.contracts import SpeechAdaptationJob


class SpeechCheckpointStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def validate_and_bind(self, job: SpeechAdaptationJob, checkpoint: SpeechCheckpoint) -> SpeechCheckpoint:
        path = checkpoint.path.resolve(strict=True)
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise SpeechTrainingBackendError(
                "speech_checkpoint_boundary_violation",
                "checkpoint escaped the worker checkpoint root",
            ) from exc
        if checkpoint.step < 1 or checkpoint.step > job.configuration.max_steps:
            raise SpeechTrainingBackendError("speech_checkpoint_step_invalid", "checkpoint step is outside the job")
        size = path.stat().st_size
        if size <= 0 or size > job.budget.max_disk_bytes:
            raise SpeechTrainingBackendError("speech_checkpoint_budget_exceeded", "checkpoint exceeds its disk budget")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != checkpoint.sha256:
            raise SpeechTrainingBackendError("speech_checkpoint_digest_mismatch", "checkpoint digest changed")
        return checkpoint

    def resolve_resume(self, job: SpeechAdaptationJob) -> Path | None:
        if job.resume is None:
            return None
        # The Hub stages the admitted checkpoint under its digest.  No path is
        # accepted from a peer or from the job payload.
        candidate = (self._root / "resume" / f"{job.resume.checkpoint_digest}.checkpoint").resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:  # pragma: no cover - defensive after fixed composition
            raise SpeechTrainingBackendError(
                "speech_resume_boundary_violation",
                "resume checkpoint escaped root",
            ) from exc
        if not candidate.is_file():
            raise SpeechTrainingBackendError(
                "speech_resume_checkpoint_missing",
                "admitted resume checkpoint is not staged",
                retryable=True,
            )
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != job.resume.checkpoint_digest:
            raise SpeechTrainingBackendError("speech_resume_checkpoint_mismatch", "resume checkpoint digest changed")
        return candidate
