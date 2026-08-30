"""Closed, code-free evaluation tasks for automatic research gates."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.research_training import canonical_digest, require_id


class ResearchEvaluationTaskRunner:
    def execute(self, *, task: Mapping[str, Any], examples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if set(task) != {"task_id", "task_version", "task_kind", "case_sensitive"}:
            raise ValueError("research_evaluation_task_fields_invalid")
        kind = str(task.get("task_kind") or "").strip().lower()
        if kind not in {"exact_match", "numeric_tolerance"}:
            raise PermissionError("research_evaluation_code_execution_denied")
        if not isinstance(task.get("case_sensitive"), bool) or not 1 <= len(examples) <= 10_000:
            raise ValueError("research_evaluation_task_invalid")
        passed = 0
        for example in examples:
            if set(example) != {"prediction", "expected", "tolerance"}:
                raise ValueError("research_evaluation_example_fields_invalid")
            if kind == "exact_match":
                prediction = str(example["prediction"])
                expected = str(example["expected"])
                if task["case_sensitive"] is False:
                    prediction, expected = prediction.casefold(), expected.casefold()
                success = prediction == expected
            else:
                tolerance = float(example["tolerance"])
                if not math.isfinite(tolerance) or tolerance < 0:
                    raise ValueError("research_evaluation_tolerance_invalid")
                success = abs(float(example["prediction"]) - float(example["expected"])) <= tolerance
            passed += int(success)
        result = {
            "schema": "ananta.research-training-task-result.v1",
            "task_id": require_id(task.get("task_id"), "task_id"),
            "task_version": require_id(task.get("task_version"), "task_version"),
            "total": len(examples),
            "passed": passed,
            "accuracy": passed / len(examples),
            "code_execution_performed": False,
            "human_intervention_required": False,
        }
        result["result_digest"] = canonical_digest(result)
        return result


__all__ = ["ResearchEvaluationTaskRunner"]
