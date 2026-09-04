"""Deterministic metrics for the three allowlisted optimization use cases."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.dspy_optimization import canonical_digest
from worker.optimization.dspy.lm_bridge import AnantaBaseLmBridge


class DspyDeterministicMetricBridge:
    def evaluate(
        self,
        *,
        program_kind: str,
        expected: Mapping[str, Any],
        actual: Mapping[str, Any],
        allowed_source_refs: Sequence[str] = (),
    ) -> dict[str, Any]:
        if program_kind == "planning_structured_tasks":
            score, violations = self._planning(expected, actual)
        elif program_kind == "rag_answer":
            score, violations = self._rag(expected, actual, frozenset(allowed_source_refs))
        elif program_kind == "structured_extraction":
            score, violations = self._extraction(expected, actual)
        else:
            raise ValueError("dspy_metric_program_kind_invalid")
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("dspy_metric_value_invalid")
        result = {
            "schema": "ananta.dspy-deterministic-metric.v1",
            "program_kind": program_kind,
            "score": score,
            "passed": not violations,
            "reason_codes": violations,
            "expected_digest": canonical_digest(expected),
            "actual_digest": canonical_digest(actual),
        }
        result["metric_digest"] = canonical_digest(result)
        return result

    @staticmethod
    def _planning(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> tuple[float, list[str]]:
        expected_tasks = expected.get("tasks")
        actual_tasks = actual.get("tasks")
        if not isinstance(expected_tasks, list) or not isinstance(actual_tasks, list):
            return 0.0, ["dspy_planning_tasks_invalid"]
        violations: list[str] = []
        ids: set[str] = set()
        for task in actual_tasks:
            if not isinstance(task, Mapping) or set(task) - {"id", "title", "description", "depends_on"}:
                violations.append("dspy_planning_task_schema_invalid")
                continue
            task_id = str(task.get("id") or "")
            if not task_id or task_id in ids or not str(task.get("title") or ""):
                violations.append("dspy_planning_task_identity_invalid")
            dependencies = task.get("depends_on") or []
            if not isinstance(dependencies, list) or any(str(value) not in ids for value in dependencies):
                violations.append("dspy_planning_dependency_invalid")
            ids.add(task_id)
        coverage = min(len(actual_tasks), len(expected_tasks)) / max(len(expected_tasks), 1)
        return coverage if not violations else 0.0, sorted(set(violations))

    @staticmethod
    def _rag(
        expected: Mapping[str, Any], actual: Mapping[str, Any], allowed_source_refs: frozenset[str]
    ) -> tuple[float, list[str]]:
        if set(actual) != {"answer", "citations"} or not isinstance(actual.get("answer"), str):
            return 0.0, ["dspy_rag_answer_schema_invalid"]
        citations = actual.get("citations")
        if not isinstance(citations, list) or any(str(value) not in allowed_source_refs for value in citations):
            return 0.0, ["dspy_rag_citation_binding_invalid"]
        required = set(str(value) for value in expected.get("citations") or ())
        coverage = len(required & set(citations)) / max(len(required), 1)
        return coverage, [] if coverage == 1 else ["dspy_rag_citation_coverage_incomplete"]

    @staticmethod
    def _extraction(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> tuple[float, list[str]]:
        if set(actual) != {"result"} or not isinstance(actual.get("result"), Mapping):
            return 0.0, ["dspy_extraction_schema_invalid"]
        expected_result = expected.get("result")
        actual_result = actual["result"]
        if not isinstance(expected_result, Mapping) or set(actual_result) != set(expected_result):
            return 0.0, ["dspy_extraction_fields_invalid"]
        correct = sum(actual_result[key] == value for key, value in expected_result.items())
        return correct / max(len(expected_result), 1), [] if correct == len(expected_result) else [
            "dspy_extraction_value_mismatch"
        ]


class DspySemanticJudgeMetricBridge:
    """Optional authorized judge that can only narrow a deterministic success."""

    def __init__(self, lm: AnantaBaseLmBridge, *, minimum_score: float = 0.8) -> None:
        if not math.isfinite(minimum_score) or not 0 <= minimum_score <= 1:
            raise ValueError("dspy_semantic_threshold_invalid")
        self._lm = lm
        self._minimum = minimum_score

    def evaluate(
        self,
        *,
        deterministic: Mapping[str, Any],
        expected: Mapping[str, Any],
        actual: Mapping[str, Any],
        call_index: int,
    ) -> dict[str, Any]:
        if deterministic.get("passed") is not True:
            return {
                "passed": False,
                "score": None,
                "reason_codes": ["dspy_semantic_judge_skipped_deterministic_failure"],
                "model_call_performed": False,
            }
        response = self._lm.complete(
            role="judge",
            messages=(
                {
                    "role": "system",
                    "content": (
                        "Score semantic quality from 0 to 1. "
                        "Return strict JSON with fields score and reason_codes."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"expected": expected, "actual": actual},
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        allow_nan=False,
                    ),
                },
            ),
            call_index=call_index,
        )
        try:
            value = json.loads(response["text"])
            score = float(value["score"])
            reason_codes = [str(item) for item in value.get("reason_codes", [])]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("dspy_semantic_judge_response_invalid") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"score", "reason_codes"}
            or not isinstance(value["reason_codes"], list)
            or len(reason_codes) > 16
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise ValueError("dspy_semantic_judge_response_invalid")
        passed = score >= self._minimum
        return {
            "passed": passed,
            "score": score,
            "reason_codes": reason_codes if not passed else [],
            "model_call_performed": True,
            "request_digest": response["request_digest"],
        }


__all__ = ["DspyDeterministicMetricBridge", "DspySemanticJudgeMetricBridge"]
