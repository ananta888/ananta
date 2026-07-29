"""Small substitutable port implemented by every training engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from worker.training.contracts import TrainingJobRequest
from worker.training.datasets import VerifiedDataset
from worker.training.process_control import CancellationToken

ProgressCallback = Callable[[str, Mapping[str, Any]], None]


class TrainingBackendError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class TrainingContext:
    request: TrainingJobRequest
    dataset: VerifiedDataset
    model_path: Path
    artifact_root: Path
    checkpoint_root: Path
    resume_path: Path | None
    cancel: CancellationToken
    emit: ProgressCallback
    checkpoint_state_root: Path | None = None


@dataclass(frozen=True)
class TrainingOutcome:
    metrics: Mapping[str, Any]
    artifacts: tuple[Path, ...]
    best_checkpoint: Path | None = None


class TrainingBackend(Protocol):
    name: str

    def availability(self) -> tuple[bool, str | None]: ...

    def prepare(self, context: TrainingContext) -> Any: ...

    def train(self, context: TrainingContext, prepared: Any) -> Any: ...

    def evaluate(self, context: TrainingContext, prepared: Any, trained: Any) -> Mapping[str, Any]: ...

    def save(
        self,
        context: TrainingContext,
        prepared: Any,
        trained: Any,
        metrics: Mapping[str, Any],
    ) -> TrainingOutcome: ...


def run_backend(backend: TrainingBackend, context: TrainingContext) -> TrainingOutcome:
    context.cancel.raise_if_cancelled()
    checkpoint_session = None
    checkpoint_lifecycle = getattr(backend, "checkpoint_lifecycle", None)
    if checkpoint_lifecycle is not None:
        checkpoint_session = checkpoint_lifecycle.bind(context)
        context = checkpoint_session.context
        context.cancel.raise_if_cancelled()
    prepared = backend.prepare(context)
    context.cancel.raise_if_cancelled()
    trained = backend.train(context, prepared)
    context.cancel.raise_if_cancelled()
    metrics = backend.evaluate(context, prepared, trained)
    context.cancel.raise_if_cancelled()
    outcome = backend.save(context, prepared, trained, metrics)
    if checkpoint_session is not None:
        return checkpoint_session.finalize(outcome)
    return outcome
