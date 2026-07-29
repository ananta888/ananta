"""Unsloth vision strategy with an independent backend identity."""

from __future__ import annotations

from typing import Any, Mapping

from worker.training.backends.base import TrainingContext, TrainingOutcome
from worker.training.backends.unsloth_multimodal import UnslothMultimodalEngine


class UnslothVisionTrainingBackend:
    name = "unsloth_vision"

    def __init__(self) -> None:
        self._engine = UnslothMultimodalEngine(
            backend_name=self.name,
            model_class_name="FastVisionModel",
            media_type="image",
        )
        self.checkpoint_lifecycle = self._engine.checkpoint_lifecycle

    def availability(self) -> tuple[bool, str | None]:
        return self._engine.availability()

    def prepare(self, context: TrainingContext) -> Any:
        return self._engine.prepare(context)

    def train(self, context: TrainingContext, prepared: Any) -> Any:
        return self._engine.train(context, prepared)

    def evaluate(self, context: TrainingContext, prepared: Any, trained: Any) -> Mapping[str, Any]:
        return self._engine.evaluate(context, prepared, trained)

    def save(
        self,
        context: TrainingContext,
        prepared: Any,
        trained: Any,
        metrics: Mapping[str, Any],
    ) -> TrainingOutcome:
        return self._engine.save(context, prepared, trained, metrics)
