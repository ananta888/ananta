"""Quantitative Hub Go/No-Go for Spreadsheet Studio training."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from agent.services.spreadsheet_learning_repository_port import SpreadsheetLearningRepository
from agent.services.spreadsheet_learning_service import SpreadsheetLearningService
from ananta_contracts.spreadsheet_studio import canonical_digest, require_digest, require_id


class SpreadsheetTrainingAdmissionService:
    """Binds immutable baseline, dataset readiness and resource evidence."""

    POLICY_VERSION = "spreadsheet-training-admission.v1"
    THRESHOLDS = {
        "baseline_min_samples": 20,
        "baseline_min_schema_valid_rate": 1.0,
        "baseline_min_action_valid_rate": 0.95,
        "baseline_min_safe_rejection_rate": 1.0,
        "baseline_min_safe_rejection_cases": 1,
        "baseline_min_execution_success_rate": 0.95,
        "baseline_min_validator_pass_rate": 0.95,
        "baseline_max_unintended_change_rate": 0.01,
        "dataset_min_records": 100,
        "dataset_min_lineage_roots": 5,
        "dataset_min_instruction_templates": 5,
        "dataset_min_leakage_clusters": 5,
        "resource_min_context_tokens": 2_048,
    }

    def __init__(
        self,
        *,
        learning: SpreadsheetLearningService,
        repository: SpreadsheetLearningRepository,
        clock=time.time,
    ) -> None:
        self._learning = learning
        self._repository = repository
        self._clock = clock

    def create_baseline(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        required = {"schema", "baseline_id", "base_model", "model_digest", "evaluation_report"}
        if set(payload) != required or payload.get("schema") != "ananta.spreadsheet-baseline-command.v1":
            raise ValueError("spreadsheet_baseline_fields_invalid")
        report = self._evaluation_report(payload.get("evaluation_report"))
        base_model = self._text(payload.get("base_model"), "spreadsheet_baseline_base_model_invalid", maximum=512)
        metrics = self._baseline_metrics(report)
        baseline = {
            "schema": "ananta.spreadsheet-base-model-baseline.v1",
            "baseline_id": require_id(payload.get("baseline_id"), "baseline_id"),
            "owner_id": require_id(principal_id, "principal_id"),
            "base_model": base_model,
            "model_digest": require_digest(payload.get("model_digest"), "model_digest"),
            "evaluation_report_digest": report["report_digest"],
            "evaluation_bindings": dict(report["bindings"]),
            "sample_count": int(report["summary"]["sample_count"]),
            "metrics": metrics,
            "policy_version": self.POLICY_VERSION,
            "created_at": float(self._clock()),
            "human_intervention_required": False,
        }
        baseline["baseline_digest"] = canonical_digest(baseline)
        return self._repository.append_baseline(tenant_id, baseline)

    def get_baseline(self, *, tenant_id: str, principal_id: str, baseline_id: str) -> dict[str, Any]:
        baseline = self._repository.get_baseline(tenant_id, baseline_id)
        if baseline.get("owner_id") != principal_id:
            raise PermissionError("spreadsheet_baseline_owner_required")
        return baseline

    def admit(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        required = {"schema", "admission_id", "dataset_id", "baseline_id", "base_model", "resource_profile"}
        if set(payload) != required or payload.get("schema") != "ananta.spreadsheet-training-admission-command.v1":
            raise ValueError("spreadsheet_training_admission_fields_invalid")
        dataset_id = require_id(payload.get("dataset_id"), "dataset_id")
        dataset = self._learning.get_dataset(
            tenant_id=tenant_id,
            principal_id=principal_id,
            dataset_id=dataset_id,
        )
        baseline = self.get_baseline(
            tenant_id=tenant_id,
            principal_id=principal_id,
            baseline_id=str(payload.get("baseline_id") or ""),
        )
        base_model = self._text(payload.get("base_model"), "spreadsheet_admission_base_model_invalid", maximum=512)
        resource = self._resource_profile(payload.get("resource_profile"))
        reasons = self._baseline_reasons(baseline)
        reasons.extend(self._dataset_reasons(dataset))
        reasons.extend(self._resource_reasons(resource, base_model))
        if baseline.get("base_model") != base_model:
            reasons.append("spreadsheet_admission_baseline_model_mismatch")
        reasons = sorted(set(reasons))
        admission = {
            "schema": "ananta.spreadsheet-training-admission.v1",
            "admission_id": require_id(payload.get("admission_id"), "admission_id"),
            "owner_id": principal_id,
            "dataset_id": dataset_id,
            "dataset_digest": dataset["dataset_digest"],
            "dataset_manifest_digest": dataset["digest"],
            "split_lock_digest": dataset["split_lock"]["split_lock_digest"],
            "baseline_id": baseline["baseline_id"],
            "baseline_digest": baseline["baseline_digest"],
            "base_model": base_model,
            "model_digest": baseline["model_digest"],
            "resource_profile_id": resource["profile_id"],
            "resource_profile_digest": resource["profile_digest"],
            "policy_version": self.POLICY_VERSION,
            "thresholds": dict(self.THRESHOLDS),
            "decision": "go" if not reasons else "no_go",
            "reason_codes": reasons,
            "alternative_path": {
                "available": True,
                "mode": "base_model_only",
                "reason_code": None if not reasons else "spreadsheet_training_not_required_for_product_path",
            },
            "created_at": float(self._clock()),
            "human_intervention_required": False,
        }
        admission["admission_digest"] = canonical_digest(admission)
        return self._repository.append_training_admission(tenant_id, admission)

    def require_go(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        admission_id: str,
        dataset_id: str,
        base_model: str,
    ) -> dict[str, Any]:
        admission = self.get_admission(
            tenant_id=tenant_id,
            principal_id=principal_id,
            admission_id=admission_id,
        )
        if admission.get("decision") != "go":
            raise PermissionError("spreadsheet_training_admission_no_go")
        dataset = self._learning.get_dataset(
            tenant_id=tenant_id,
            principal_id=principal_id,
            dataset_id=dataset_id,
        )
        if (
            admission.get("dataset_id") != dataset_id
            or admission.get("base_model") != base_model
            or admission.get("dataset_digest") != dataset.get("dataset_digest")
            or admission.get("dataset_manifest_digest") != dataset.get("digest")
            or admission.get("split_lock_digest") != dict(dataset.get("split_lock") or {}).get("split_lock_digest")
        ):
            raise PermissionError("spreadsheet_training_admission_binding_stale")
        return admission

    def get_admission(self, *, tenant_id: str, principal_id: str, admission_id: str) -> dict[str, Any]:
        admission = self._repository.get_training_admission(tenant_id, admission_id)
        if admission.get("owner_id") != principal_id:
            raise PermissionError("spreadsheet_training_admission_owner_required")
        return admission

    def _baseline_reasons(self, baseline: Mapping[str, Any]) -> list[str]:
        metrics = dict(baseline.get("metrics") or {})
        checks = (
            (int(baseline.get("sample_count") or 0) >= self.THRESHOLDS["baseline_min_samples"], "samples"),
            (metrics.get("schema_valid_rate", 0) >= self.THRESHOLDS["baseline_min_schema_valid_rate"], "schema"),
            (metrics.get("action_valid_rate", 0) >= self.THRESHOLDS["baseline_min_action_valid_rate"], "actions"),
            (
                metrics.get("safe_rejection_rate", 0) >= self.THRESHOLDS["baseline_min_safe_rejection_rate"]
                and metrics.get("safe_rejection_case_count", 0)
                >= self.THRESHOLDS["baseline_min_safe_rejection_cases"],
                "safe_rejection",
            ),
            (
                metrics.get("execution_success_rate", 0)
                >= self.THRESHOLDS["baseline_min_execution_success_rate"],
                "execution",
            ),
            (
                metrics.get("validator_pass_rate", 0) >= self.THRESHOLDS["baseline_min_validator_pass_rate"],
                "validators",
            ),
            (
                metrics.get("unintended_change_rate", 1)
                <= self.THRESHOLDS["baseline_max_unintended_change_rate"],
                "unintended_changes",
            ),
        )
        return [f"spreadsheet_baseline_{name}_threshold_failed" for passed, name in checks if not passed]

    def _dataset_reasons(self, dataset: Mapping[str, Any]) -> list[str]:
        split_lock = dict(dataset.get("split_lock") or {})
        diversity = dict(split_lock.get("diversity") or {})
        split_counts = dict(dataset.get("split_counts") or {})
        record_count = int(dataset.get("record_count") or 0)
        consent_count = len(list(dataset.get("consent_refs") or []))
        recipe = dict(dataset.get("recipe_manifest") or {})
        reasons = []
        checks = (
            (record_count >= self.THRESHOLDS["dataset_min_records"], "minimum_records_not_met"),
            (
                diversity.get("lineage_root_count", 0) >= self.THRESHOLDS["dataset_min_lineage_roots"],
                "lineage_diversity_not_met",
            ),
            (
                diversity.get("instruction_template_count", 0)
                >= self.THRESHOLDS["dataset_min_instruction_templates"],
                "template_diversity_not_met",
            ),
            (
                diversity.get("leakage_cluster_count", 0) >= self.THRESHOLDS["dataset_min_leakage_clusters"],
                "cluster_diversity_not_met",
            ),
            (
                all(int(split_counts.get(name) or 0) > 0 for name in ("train", "validation", "eval", "test")),
                "split_empty",
            ),
            (consent_count == record_count, "consent_coverage_incomplete"),
            (dataset.get("masking_version") == "spreadsheet-masking.v1", "privacy_policy_unbound"),
            (recipe.get("license_policy") == "owner-submitted-consent.v1", "license_policy_unbound"),
            (recipe.get("tenant_pooling") == "forbidden", "tenant_pooling_invalid"),
            (set(recipe.get("task_kinds") or []) == {"spreadsheet_actions"}, "task_kind_invalid"),
        )
        reasons.extend(f"spreadsheet_dataset_{name}" for passed, name in checks if not passed)
        if dataset.get("state") == "quarantined":
            reasons.append("spreadsheet_dataset_quarantined")
        return reasons

    def _resource_reasons(self, resource: Mapping[str, Any], base_model: str) -> list[str]:
        reasons = []
        if resource.get("available") is not True:
            reasons.append("spreadsheet_resource_profile_unavailable")
        if base_model not in set(resource.get("supported_base_models") or []):
            reasons.append("spreadsheet_resource_profile_model_unsupported")
        if int(resource.get("max_context_tokens") or 0) < self.THRESHOLDS["resource_min_context_tokens"]:
            reasons.append("spreadsheet_resource_profile_context_insufficient")
        return reasons

    @staticmethod
    def _baseline_metrics(report: Mapping[str, Any]) -> dict[str, Any]:
        metrics = dict(dict(report["summary"])["base"])
        required = {
            "schema_valid_rate",
            "action_valid_rate",
            "safe_rejection_rate",
            "safe_rejection_case_count",
            "safe_policy_rate",
            "execution_success_rate",
            "validator_pass_rate",
            "unintended_change_rate",
            "score",
        }
        if set(metrics) != required:
            raise ValueError("spreadsheet_baseline_metrics_invalid")
        rate_fields = required - {"safe_rejection_case_count"}
        if any(
            isinstance(metrics[field], bool)
            or not isinstance(metrics[field], (int, float))
            or not 0 <= float(metrics[field]) <= 1
            for field in rate_fields
        ):
            raise ValueError("spreadsheet_baseline_metrics_invalid")
        case_count = metrics["safe_rejection_case_count"]
        if isinstance(case_count, bool) or not isinstance(case_count, int) or case_count < 0:
            raise ValueError("spreadsheet_baseline_metrics_invalid")
        return {
            field: case_count if field == "safe_rejection_case_count" else float(metrics[field])
            for field in sorted(required)
        }

    @staticmethod
    def _evaluation_report(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("spreadsheet_baseline_report_invalid")
        report = dict(value)
        supplied = require_digest(report.pop("report_digest", None), "report_digest")
        if canonical_digest(report) != supplied:
            raise ValueError("spreadsheet_baseline_report_digest_mismatch")
        report["report_digest"] = supplied
        bindings = report.get("bindings")
        summary = report.get("summary")
        if (
            report.get("schema") != "ananta.spreadsheet-evaluation-report.v1"
            or report.get("mode") != "non_publishing"
            or report.get("published_candidates") != 0
            or report.get("feedback_events") != 0
            or report.get("consent_events") != 0
            or not isinstance(bindings, Mapping)
            or set(bindings)
            != {"engine_version", "sample_digest", "policy_digest", "output_schema_digest", "serializer_digest"}
            or bindings.get("engine_version") != "spreadsheet-execution-evaluation.v2"
            or not isinstance(summary, Mapping)
            or isinstance(summary.get("sample_count"), bool)
            or not isinstance(summary.get("sample_count"), int)
            or not 1 <= summary["sample_count"] <= 10_000
        ):
            raise ValueError("spreadsheet_baseline_report_invalid")
        for field in ("sample_digest", "policy_digest", "output_schema_digest", "serializer_digest"):
            require_digest(bindings.get(field), field)
        return report

    @staticmethod
    def _resource_profile(value: Any) -> dict[str, Any]:
        required = {
            "schema",
            "profile_id",
            "backend",
            "available",
            "supported_base_models",
            "max_context_tokens",
            "gpu_memory_bytes",
            "profile_digest",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("spreadsheet_resource_profile_invalid")
        profile = dict(value)
        supplied = require_digest(profile.pop("profile_digest", None), "profile_digest")
        if canonical_digest(profile) != supplied:
            raise ValueError("spreadsheet_resource_profile_digest_mismatch")
        models = profile.get("supported_base_models")
        if (
            profile.get("schema") != "ananta.spreadsheet-training-resource-profile.v1"
            or not isinstance(profile.get("available"), bool)
            or not isinstance(models, list)
            or not 1 <= len(models) <= 100
            or any(not isinstance(model, str) or not model.strip() or len(model) > 512 for model in models)
            or len(set(models)) != len(models)
            or isinstance(profile.get("max_context_tokens"), bool)
            or not isinstance(profile.get("max_context_tokens"), int)
            or not 1 <= profile["max_context_tokens"] <= 10_000_000
            or isinstance(profile.get("gpu_memory_bytes"), bool)
            or not isinstance(profile.get("gpu_memory_bytes"), int)
            or not 0 <= profile["gpu_memory_bytes"] <= 10**15
        ):
            raise ValueError("spreadsheet_resource_profile_invalid")
        require_id(profile.get("profile_id"), "profile_id")
        SpreadsheetTrainingAdmissionService._text(
            profile.get("backend"), "spreadsheet_resource_profile_invalid", maximum=128
        )
        return {**profile, "profile_digest": supplied}

    @staticmethod
    def _text(value: Any, reason: str, *, maximum: int) -> str:
        normalized = str(value or "").strip()
        if not 1 <= len(normalized) <= maximum or any(ord(character) < 32 for character in normalized):
            raise ValueError(reason)
        return normalized


__all__ = ["SpreadsheetTrainingAdmissionService"]
