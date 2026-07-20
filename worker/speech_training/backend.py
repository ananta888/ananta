"""Small substitutable backend port for speech adaptation workers.

Backends execute bounded model operations.  They never create Hub tasks,
mutate consent, contact peers, or publish artifacts directly.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from worker.speech_training.contracts import SpeechAdaptationJob

ContentFreeEventSink = Callable[[str, Mapping[str, Any]], None]


class SpeechTrainingBackendError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.code = reason_code
        self.retryable = retryable


class SpeechTrainingAborted(SpeechTrainingBackendError):
    pass


class AbortSignal:
    """Thread-safe cancellation/lease/revocation signal shared with one job."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason_code = "speech_training_cancelled"

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    @property
    def reason_code(self) -> str:
        return self._reason_code

    def abort(self, reason_code: str = "speech_training_cancelled") -> None:
        normalized = str(reason_code or "").strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("abort reason_code must be bounded")
        if not self._event.is_set():
            self._reason_code = normalized
            self._event.set()

    def raise_if_aborted(self) -> None:
        if self.aborted:
            raise SpeechTrainingAborted(self.reason_code, "speech training execution was aborted")


@dataclass(frozen=True)
class SpeechDatasetView:
    """Worker-local view opened only after Hub bindings were revalidated."""

    root: Path
    dataset_digest: str
    split_digest: str
    train_sample_count: int
    validation_sample_count: int


@dataclass(frozen=True)
class SpeechTrainingContext:
    job: SpeechAdaptationJob
    dataset: SpeechDatasetView
    model_root: Path
    workspace_root: Path
    checkpoint_root: Path
    artifact_root: Path
    abort: AbortSignal
    emit: ContentFreeEventSink
    clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000

    def enforce_active(self) -> None:
        self.abort.raise_if_aborted()
        now = int(self.clock_ms())
        if now >= self.job.deadline_at_ms:
            raise SpeechTrainingAborted("speech_deadline_expired", "speech training deadline expired")
        if now >= self.job.fencing.lease_expires_at_ms:
            raise SpeechTrainingAborted("speech_lease_expired", "speech training lease expired")


@dataclass(frozen=True)
class PreparedSpeechTraining:
    preparation_digest: str
    resume_step: int


@dataclass(frozen=True)
class SpeechTrainingState:
    completed_steps: int
    state_digest: str


@dataclass(frozen=True)
class SpeechCheckpoint:
    step: int
    path: Path
    sha256: str


@dataclass(frozen=True)
class SpeechAdapterArtifact:
    path: Path
    sha256: str
    size_bytes: int
    media_type: str = "application/vnd.ananta.speech-adapter"


class SpeechTrainingBackend(Protocol):
    name: str

    def availability(self) -> tuple[bool, str | None]: ...

    def validate(self, context: SpeechTrainingContext) -> None: ...

    def prepare(self, context: SpeechTrainingContext) -> PreparedSpeechTraining: ...

    def train(
        self,
        context: SpeechTrainingContext,
        prepared: PreparedSpeechTraining,
    ) -> SpeechTrainingState: ...

    def checkpoint(
        self,
        context: SpeechTrainingContext,
        state: SpeechTrainingState,
    ) -> SpeechCheckpoint: ...

    def evaluate(
        self,
        context: SpeechTrainingContext,
        state: SpeechTrainingState,
    ) -> Mapping[str, Any]: ...

    def export(
        self,
        context: SpeechTrainingContext,
        state: SpeechTrainingState,
        evaluation: Mapping[str, Any],
    ) -> SpeechAdapterArtifact: ...

    def cleanup(self, context: SpeechTrainingContext, *, succeeded: bool) -> None: ...
