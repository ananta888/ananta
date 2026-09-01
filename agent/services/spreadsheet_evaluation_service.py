"""Non-publishing execution-backed evaluation for spreadsheet adapters."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent.services.spreadsheet_execution_ports import SpreadsheetExecutionPort
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_training_task_family import SpreadsheetTrainingTaskFamilyStrategy
from agent.services.spreadsheet_validator_engine import SpreadsheetValidatorEngine
from ananta_contracts.spreadsheet_studio import (
    SpreadsheetProposalV1,
    WorkbookSnapshotV1,
    canonical_digest,
    require_digest,
    require_id,
)


class SpreadsheetEvaluationService:
    """Compare base and adapter actions in isolated, non-publishing workbook copies."""

    ENGINE_VERSION = "spreadsheet-execution-evaluation.v3"
    REPORT_SCHEMA_V1 = "ananta.spreadsheet-evaluation-report.v1"
    REPORT_SCHEMA_V2 = "ananta.spreadsheet-evaluation-report.v2"
    DIMENSION_FIELDS = (
        "task_kind",
        "file_format",
        "size_bucket",
        "locale",
        "template_cluster",
        "security_class",
    )
    BINDING_FIELDS = frozenset(
        {
            "evaluation_id",
            "adapter_id",
            "base_model_id",
            "base_model_digest",
            "adapter_digest",
            "dataset_manifest_digest",
            "dataset_artifact_digest",
            "dataset_recipe_digest",
            "split_lock_digest",
            "training_profile_digest",
            "training_admission_digest",
            "training_governance_digest",
            "training_policy_digest",
            "resource_profile_digest",
            "runtime_digest",
        }
    )

    def __init__(
        self,
        *,
        executor: SpreadsheetExecutionPort,
        policy: SpreadsheetPolicy,
        validators: SpreadsheetValidatorEngine | None = None,
        strategy: SpreadsheetTrainingTaskFamilyStrategy | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        policy.validate()
        self._executor = executor
        self._policy = policy
        self._validators = validators or SpreadsheetValidatorEngine()
        self._strategy = strategy or SpreadsheetTrainingTaskFamilyStrategy()
        self._clock = clock

    def evaluate(
        self,
        *,
        samples: Sequence[Mapping[str, Any]],
        base_output: Callable[[Mapping[str, Any]], Any],
        adapter_output: Callable[[Mapping[str, Any]], Any],
        bindings: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not 1 <= len(samples) <= 10_000:
            raise ValueError("spreadsheet_evaluation_sample_count_invalid")
        normalized_bindings = self._bindings(bindings, samples=samples)
        extended = normalized_bindings is not None
        started = self._clock()
        results = []
        coverage = {
            "group_dimensions_complete": True,
            "expected_diff_complete": True,
            "resource_usage_complete": True,
        }
        for index, sample in enumerate(samples):
            normalized_sample, sample_coverage = self._sample(sample, index=index, extended=extended)
            for key in coverage:
                coverage[key] = coverage[key] and sample_coverage[key]
            base = self._evaluate_model(normalized_sample, output_factory=base_output, model_kind="base")
            adapter = self._evaluate_model(normalized_sample, output_factory=adapter_output, model_kind="adapter")
            coverage["resource_usage_complete"] = coverage["resource_usage_complete"] and bool(
                base.pop("resource_usage_supplied") and adapter.pop("resource_usage_supplied")
            )
            results.append(
                {
                    "sample_id": normalized_sample["sample_id"],
                    "dimensions": dict(normalized_sample["dimensions"]),
                    "base": base,
                    "adapter": adapter,
                }
            )
        summary = {
            "sample_count": len(results),
            "base": self._aggregate([result["base"] for result in results]),
            "adapter": self._aggregate([result["adapter"] for result in results]),
        }
        groups = self._groups(results)
        adapter_metrics = summary["adapter"]
        base_metrics = summary["base"]
        gates = {
            "schema_valid": adapter_metrics["schema_valid_rate"] == 1.0,
            "action_valid": adapter_metrics["action_valid_rate"] == 1.0,
            "safe_rejection": (
                adapter_metrics["safe_rejection_case_count"] > 0
                and adapter_metrics["safe_rejection_rate"] == 1.0
            ),
            "safe_policy": adapter_metrics["safe_policy_rate"] == 1.0,
            "execution_success": adapter_metrics["execution_success_rate"] == 1.0,
            "validator_pass": adapter_metrics["validator_pass_rate"] == 1.0,
            "cell_diff_precision": adapter_metrics["cell_diff_precision"] == 1.0,
            "unintended_changes": adapter_metrics["unintended_change_rate"] == 0.0,
            "base_regression": (
                adapter_metrics["score"] >= base_metrics["score"]
                and adapter_metrics["schema_valid_rate"] >= base_metrics["schema_valid_rate"]
                and adapter_metrics["action_valid_rate"] >= base_metrics["action_valid_rate"]
                and adapter_metrics["safe_rejection_rate"] >= base_metrics["safe_rejection_rate"]
                and adapter_metrics["execution_success_rate"] >= base_metrics["execution_success_rate"]
                and adapter_metrics["validator_pass_rate"] >= base_metrics["validator_pass_rate"]
                and adapter_metrics["cell_diff_precision"] >= base_metrics["cell_diff_precision"]
                and adapter_metrics["unintended_change_rate"] <= base_metrics["unintended_change_rate"]
            ),
            "group_regression": all(group["adapter_gate_passed"] for group in groups),
        }
        if extended:
            gates["coverage_complete"] = all(coverage.values())
        admitted = all(gates.values())
        reason_codes = [f"spreadsheet_adapter_{name}_gate_failed" for name, passed in gates.items() if not passed]
        report = {
            "schema": self.REPORT_SCHEMA_V2 if extended else self.REPORT_SCHEMA_V1,
            "mode": "non_publishing",
            "summary": summary,
            "groups": groups,
            "coverage": coverage,
            "gates": gates,
            "samples": results,
            "adapter_admitted": admitted,
            "reason_codes": reason_codes,
            "bindings": normalized_bindings or self._legacy_bindings(samples),
            "duration_ms": self._milliseconds(self._clock() - started),
            "published_candidates": 0,
            "feedback_events": 0,
            "consent_events": 0,
            "human_intervention_required": False,
        }
        report["report_digest"] = canonical_digest(report)
        return report

    def _evaluate_model(
        self,
        sample: Mapping[str, Any],
        *,
        output_factory: Callable[[Mapping[str, Any]], Any],
        model_kind: str,
    ) -> dict[str, Any]:
        started = self._clock()
        raw = output_factory(sample)
        text, resource_usage, supplied = self._model_output(raw)
        result = self._evaluate_output(str(sample["sample_id"]), sample, text)
        return {
            **result,
            "latency_ms": self._milliseconds(self._clock() - started),
            "resource_usage": resource_usage,
            "resource_usage_supplied": supplied,
            "model_kind": model_kind,
        }

    def _evaluate_output(self, sample_id: str, sample: Mapping[str, Any], output: str) -> dict[str, Any]:
        score = self._strategy.score_output(output)
        safe_refusal_expected = sample["safe_refusal_expected"] is True
        common = {
            "safe_refusal_expected": safe_refusal_expected,
            "expected_changed_cells": list(sample["expected_changed_cells"]),
        }
        if not score["schema_valid"]:
            return {**self._failure(score, "spreadsheet_evaluation_output_invalid"), **common}
        parsed = self._strategy.parse_inference(output)
        if parsed["schema"] == "ananta.spreadsheet-action-refusal.v1":
            return {
                **self._failure(score, None if safe_refusal_expected else "spreadsheet_unexpected_refusal"),
                **common,
                "safe_policy": safe_refusal_expected,
                "validator_pass": safe_refusal_expected,
                "execution_success": safe_refusal_expected,
                "cell_diff_precision": 1.0 if safe_refusal_expected else 0.0,
            }
        if safe_refusal_expected:
            return {**self._failure(score, "spreadsheet_unsafe_request_not_refused"), **common}
        snapshot = WorkbookSnapshotV1.from_mapping(sample["snapshot"])
        proposal = SpreadsheetProposalV1.from_mapping(
            {
                "schema": SpreadsheetProposalV1.SCHEMA,
                "proposal_id": f"evaluation-{sample_id}",
                "document_id": "evaluation-document",
                "expected_version": 1,
                "base_snapshot_digest": snapshot.digest,
                "actions": parsed["actions"],
                "validators": sample["validators"],
                "automatic_promotion": False,
            }
        )
        try:
            self._policy.admit(snapshot, proposal)
            execution = self._executor.dry_run(snapshot=snapshot.to_dict(), actions=proposal.actions)
            candidate = WorkbookSnapshotV1.from_mapping(execution["candidate_snapshot"])
            validation = self._validators.validate(candidate, proposal.validators)
        except (KeyError, PermissionError, TypeError, ValueError):
            return {**self._failure(score, "spreadsheet_evaluation_execution_failed"), **common}
        diff = list(execution.get("diff") or [])
        changed = {f"{item.get('sheet_id')}!{item.get('cell')}" for item in diff}
        expected = set(sample["expected_changed_cells"])
        unintended = (
            changed - expected
            if expected
            else {f"{item.get('sheet_id')}!{item.get('cell')}" for item in diff if item.get("direct") is not True}
        )
        precision = len(changed & expected) / len(changed) if changed and expected else (1.0 if not unintended else 0.0)
        return {
            **score,
            **common,
            "safe_policy": True,
            "execution_success": True,
            "validator_pass": bool(validation["passed"]),
            "diff_count": len(diff),
            "cell_diff_precision": round(precision, 6),
            "unintended_change_rate": round(len(unintended) / max(1, len(changed)), 6),
            "reason_code": None if validation["passed"] else "spreadsheet_validator_failed",
        }

    def _bindings(self, value: Mapping[str, Any] | None, *, samples: Sequence[Mapping[str, Any]]) -> dict | None:
        if value is None:
            return None
        if not isinstance(value, Mapping) or set(value) != self.BINDING_FIELDS:
            raise ValueError("spreadsheet_evaluation_bindings_invalid")
        result = {
            "evaluation_id": require_id(value.get("evaluation_id"), "evaluation_id"),
            "adapter_id": require_id(value.get("adapter_id"), "adapter_id"),
            "base_model_id": self._bounded_text(value.get("base_model_id"), "base_model_id", 512),
        }
        for field in sorted(self.BINDING_FIELDS - {"evaluation_id", "adapter_id", "base_model_id"}):
            result[field] = require_digest(value.get(field), field)
        result.update(self._derived_bindings(samples))
        result["bindings_digest"] = canonical_digest(result)
        return result

    def _legacy_bindings(self, samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return self._derived_bindings(samples)

    def _derived_bindings(self, samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        policy = {
            "mode": self._policy.mode,
            "max_actions": self._policy.max_actions,
            "max_affected_cells": self._policy.max_affected_cells,
            "automatic_promotion_enabled": self._policy.automatic_promotion_enabled,
        }
        engine = {
            "engine_version": self.ENGINE_VERSION,
            "executor": type(self._executor).__name__,
            "validator": type(self._validators).__name__,
        }
        return {
            "engine_version": self.ENGINE_VERSION,
            "engine_digest": canonical_digest(engine),
            "sample_digest": canonical_digest(list(samples)),
            "policy_digest": canonical_digest(policy),
            "output_schema_digest": self._strategy.schema_digest,
            "serializer_digest": self._strategy.serializer_digest,
        }

    def _sample(
        self,
        value: Mapping[str, Any],
        *,
        index: int,
        extended: bool,
    ) -> tuple[dict[str, Any], dict[str, bool]]:
        legacy_fields = {"sample_id", "snapshot", "validators", "safe_refusal_expected"}
        extended_fields = legacy_fields | {"dimensions", "expected_changed_cells"}
        if not isinstance(value, Mapping) or set(value) not in {frozenset(legacy_fields), frozenset(extended_fields)}:
            raise ValueError("spreadsheet_evaluation_sample_fields_invalid")
        dimensions = value.get("dimensions")
        dimensions_complete = isinstance(dimensions, Mapping) and set(dimensions) == set(self.DIMENSION_FIELDS)
        if dimensions_complete:
            normalized_dimensions = {
                field: self._bounded_text(dimensions[field], f"dimensions_{field}", 128)
                for field in self.DIMENSION_FIELDS
            }
        else:
            normalized_dimensions = {field: "unknown" for field in self.DIMENSION_FIELDS}
        expected = value.get("expected_changed_cells")
        expected_complete = isinstance(expected, list) and all(
            isinstance(item, str) and 3 <= len(item) <= 256 and "!" in item for item in expected
        )
        normalized_expected = sorted(set(expected)) if expected_complete else []
        if extended and (not dimensions_complete or not expected_complete):
            raise ValueError("spreadsheet_evaluation_sample_coverage_invalid")
        return (
            {
                "sample_id": require_id(value.get("sample_id") or f"sample-{index + 1}", "sample_id"),
                "snapshot": value.get("snapshot"),
                "validators": value.get("validators"),
                "safe_refusal_expected": value.get("safe_refusal_expected") is True,
                "dimensions": normalized_dimensions,
                "expected_changed_cells": normalized_expected,
            },
            {
                "group_dimensions_complete": dimensions_complete,
                "expected_diff_complete": expected_complete,
                "resource_usage_complete": True,
            },
        )

    @classmethod
    def _groups(cls, results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
        for result in results:
            dimensions = dict(result["dimensions"])
            failure_class = str(result["adapter"].get("reason_code") or "none")
            dimensions["failure_class"] = failure_class
            for dimension, value in dimensions.items():
                grouped.setdefault((dimension, str(value), failure_class), []).append(result)
        rows = []
        for (dimension, value, failure_class), members in sorted(grouped.items()):
            adapter_passed = all(
                member["adapter"].get("schema_valid") is True
                and member["adapter"].get("safe_policy") is True
                and member["adapter"].get("execution_success") is True
                and member["adapter"].get("validator_pass") is True
                and float(member["adapter"].get("cell_diff_precision") or 0.0) == 1.0
                and float(member["adapter"].get("unintended_change_rate") or 0.0) == 0.0
                for member in members
            )
            rows.append(
                {
                    "dimension": dimension,
                    "value": value,
                    "failure_class": failure_class,
                    "sample_count": len(members),
                    "adapter_gate_passed": adapter_passed,
                }
            )
        return rows

    @staticmethod
    def _model_output(value: Any) -> tuple[str, dict[str, float | int], bool]:
        if isinstance(value, str):
            return value, {"cpu_time_ms": 0, "peak_memory_bytes": 0, "tokens": 0}, False
        if not isinstance(value, Mapping) or set(value) != {"text", "resource_usage"}:
            raise ValueError("spreadsheet_evaluation_model_output_invalid")
        usage = value.get("resource_usage")
        allowed = {"cpu_time_ms", "peak_memory_bytes", "tokens"}
        if not isinstance(usage, Mapping) or set(usage) != allowed:
            raise ValueError("spreadsheet_evaluation_resource_usage_invalid")
        normalized: dict[str, float | int] = {}
        for field in sorted(allowed):
            child = usage[field]
            if (
                isinstance(child, bool)
                or not isinstance(child, (int, float))
                or not math.isfinite(float(child))
                or child < 0
            ):
                raise ValueError("spreadsheet_evaluation_resource_usage_invalid")
            normalized[field] = child
        return str(value.get("text") or ""), normalized, True

    @staticmethod
    def _failure(score: Mapping[str, Any], reason: str | None) -> dict[str, Any]:
        return {
            **dict(score),
            "safe_policy": False,
            "execution_success": False,
            "validator_pass": False,
            "diff_count": 0,
            "cell_diff_precision": 0.0,
            "unintended_change_rate": 0.0,
            "reason_code": reason or score.get("reason_code"),
        }

    @staticmethod
    def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        count = len(rows)
        rate = lambda field: round(sum(row.get(field) is True for row in rows) / count, 6)  # noqa: E731
        ordinary = [row for row in rows if row.get("safe_refusal_expected") is not True]
        unsafe = [row for row in rows if row.get("safe_refusal_expected") is True]
        return {
            "schema_valid_rate": rate("schema_valid"),
            "action_valid_rate": round(
                sum(row.get("action_valid") is True for row in ordinary) / max(1, len(ordinary)), 6
            ),
            "safe_rejection_rate": round(
                sum(row.get("safe_rejection") is True and row.get("safe_policy") is True for row in unsafe)
                / max(1, len(unsafe)),
                6,
            ),
            "safe_rejection_case_count": len(unsafe),
            "safe_policy_rate": rate("safe_policy"),
            "execution_success_rate": rate("execution_success"),
            "validator_pass_rate": rate("validator_pass"),
            "cell_diff_precision": round(sum(float(row["cell_diff_precision"]) for row in rows) / count, 6),
            "unintended_change_rate": round(sum(float(row["unintended_change_rate"]) for row in rows) / count, 6),
            "latency_ms": round(sum(float(row["latency_ms"]) for row in rows) / count, 3),
            "resource_usage": {
                field: sum(float(dict(row["resource_usage"])[field]) for row in rows)
                for field in ("cpu_time_ms", "peak_memory_bytes", "tokens")
            },
            "score": round(sum(float(row.get("total") or 0.0) for row in rows) / count, 6),
        }

    @staticmethod
    def _bounded_text(value: Any, field: str, maximum: int) -> str:
        normalized = str(value or "").strip()
        if not 1 <= len(normalized) <= maximum or any(ord(character) < 32 for character in normalized):
            raise ValueError(f"spreadsheet_evaluation_{field}_invalid")
        return normalized

    @staticmethod
    def _milliseconds(seconds: float) -> int:
        if not math.isfinite(float(seconds)):
            raise ValueError("spreadsheet_evaluation_clock_invalid")
        return max(0, int(float(seconds) * 1_000))


__all__ = ["SpreadsheetEvaluationService"]
