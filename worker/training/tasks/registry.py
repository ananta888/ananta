"""Open/closed registry for versioned evaluation task adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.research_training import canonical_digest
from worker.training.tasks.builtin import (
    ExactMatchScorer,
    MultipleChoiceScorer,
    NumericToleranceScorer,
    PromptRenderer,
)
from worker.training.tasks.contracts import ResearchTaskDefinition, TaskRenderer, TaskScorer


class ResearchTaskRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, tuple[TaskRenderer, TaskScorer]] = {}
        renderer = PromptRenderer()
        self.register("exact_match", renderer, ExactMatchScorer())
        self.register("multiple_choice", renderer, MultipleChoiceScorer())
        self.register("numeric_tolerance", renderer, NumericToleranceScorer())

    def register(self, kind: str, renderer: TaskRenderer, scorer: TaskScorer) -> None:
        normalized = str(kind or "").strip().lower()
        if not normalized or normalized in self._adapters:
            raise ValueError("research_task_adapter_duplicate")
        self._adapters[normalized] = (renderer, scorer)

    def evaluate(
        self,
        *,
        task: Mapping[str, Any],
        examples: Sequence[Mapping[str, Any]],
        predictions: Sequence[str],
    ) -> dict[str, Any]:
        definition = ResearchTaskDefinition.from_mapping(task)
        if definition.task_kind not in self._adapters:
            raise LookupError("research_task_adapter_unavailable")
        if not examples or len(examples) != len(predictions):
            raise ValueError("research_task_predictions_invalid")
        renderer, scorer = self._adapters[definition.task_kind]
        scores: list[float] = []
        for example, prediction in zip(examples, predictions, strict=True):
            renderer.render(example)  # validates the input contract independently of scoring
            scores.append(float(scorer.score(prediction=prediction, example=example)))
        result = {
            "schema": "ananta.research-training-versioned-task-result.v1",
            "task_id": definition.task_id,
            "task_version": definition.task_version,
            "task_digest": definition.digest,
            "group": definition.group,
            "mandatory": definition.mandatory,
            "dataset_digest": definition.dataset_digest,
            "source_refs": list(definition.source_refs),
            "total": len(scores),
            "passed": sum(score >= 1.0 for score in scores),
            "score": sum(scores) / len(scores),
            "samples_persisted": False,
        }
        result["result_digest"] = canonical_digest(result)
        return result


__all__ = ["ResearchTaskRegistry"]
