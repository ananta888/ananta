"""Built-in renderers and scorers without training-core dependencies."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


class PromptRenderer:
    def render(self, example: Mapping[str, Any]) -> str:
        prompt = example.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("research_task_prompt_invalid")
        choices = example.get("choices")
        if choices is None:
            return prompt
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or not choices:
            raise ValueError("research_task_choices_invalid")
        return prompt + "\n" + "\n".join(f"{index}: {choice}" for index, choice in enumerate(choices))


class ExactMatchScorer:
    def __init__(self, *, case_sensitive: bool = True) -> None:
        self._case_sensitive = bool(case_sensitive)

    def score(self, *, prediction: str, example: Mapping[str, Any]) -> float:
        expected = str(example.get("expected") or "")
        if not self._case_sensitive:
            prediction, expected = prediction.casefold(), expected.casefold()
        return float(prediction.strip() == expected.strip())


class MultipleChoiceScorer:
    def score(self, *, prediction: str, example: Mapping[str, Any]) -> float:
        expected = example.get("expected_index")
        choices = example.get("choices")
        if (
            not isinstance(expected, int)
            or isinstance(expected, bool)
            or not isinstance(choices, Sequence)
            or isinstance(choices, (str, bytes))
            or not 0 <= expected < len(choices)
        ):
            raise ValueError("research_task_multiple_choice_invalid")
        normalized = prediction.strip().casefold()
        accepted = {str(expected), chr(ord("a") + expected), str(choices[expected]).strip().casefold()}
        return float(normalized in accepted)


class NumericToleranceScorer:
    def score(self, *, prediction: str, example: Mapping[str, Any]) -> float:
        tolerance = example.get("tolerance", 0.0)
        expected = example.get("expected")
        if (
            not isinstance(tolerance, (int, float))
            or isinstance(tolerance, bool)
            or not math.isfinite(float(tolerance))
            or float(tolerance) < 0
        ):
            raise ValueError("research_task_tolerance_invalid")
        try:
            return float(abs(float(prediction.strip()) - float(expected)) <= float(tolerance))
        except (TypeError, ValueError):
            return 0.0


__all__ = ["ExactMatchScorer", "MultipleChoiceScorer", "NumericToleranceScorer", "PromptRenderer"]
