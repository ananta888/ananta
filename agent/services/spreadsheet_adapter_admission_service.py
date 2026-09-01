"""Hub-owned Spreadsheet adapter evaluation and registry admission."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.ml_intern_training_contract import normalize_run_ids, normalize_source_ids
from ananta_contracts.spreadsheet_studio import canonical_digest, require_digest, require_id


class SpreadsheetAdapterRegistryPort(Protocol):
    """Narrow persistence port required by spreadsheet admission policy."""

    def get(self, adapter_id: str, **scope: Any) -> Any: ...

    def set_eval_report(self, adapter_id: str, **values: Any) -> Any: ...

    def promote_evaluated(self, adapter_id: str, **values: Any) -> tuple[Any, bool]: ...


class SpreadsheetAdapterAdmissionService:
    """Verify execution evidence before the existing registry performs atomic promotion."""

    COMMAND_SCHEMA = "ananta.spreadsheet-adapter-admission-command.v1"
    RESULT_SCHEMA = "ananta.spreadsheet-adapter-admission.v1"
    COMMAND_FIELDS = frozenset(
        {
            "schema",
            "adapter_id",
            "expected_registry_version",
            "evaluation_report",
            "training_governance",
            "execution_evidence",
            "reason",
        }
    )
    GOVERNANCE_FIELDS = frozenset(
        {
            "training_profile_digest",
            "base_model_digest",
            "dataset_manifest_digest",
            "dataset_artifact_digest",
            "dataset_recipe_digest",
            "split_lock_digest",
            "action_schema_digest",
            "serializer_digest",
            "policy_digest",
            "resource_profile_digest",
            "training_admission_digest",
            "governance_digest",
        }
    )
    EXECUTION_FIELDS = frozenset(
        {
            "job_id",
            "attempt_id",
            "fencing_token_digest",
            "source_ids",
            "run_ids",
            "export_sha256",
        }
    )

    def __init__(self, *, registry: SpreadsheetAdapterRegistryPort) -> None:
        self._registry = registry

    def admit(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if set(payload) != self.COMMAND_FIELDS or payload.get("schema") != self.COMMAND_SCHEMA:
            raise ValueError("spreadsheet_adapter_admission_fields_invalid")
        adapter_id = require_id(payload.get("adapter_id"), "adapter_id")
        expected_version = payload.get("expected_registry_version")
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("spreadsheet_adapter_admission_registry_version_invalid")
        reason = str(payload.get("reason") or "").strip()
        if not 10 <= len(reason) <= 500:
            raise ValueError("spreadsheet_adapter_admission_reason_invalid")
        governance = self._governance(payload.get("training_governance"))
        report = self._report(payload.get("evaluation_report"), adapter_id=adapter_id, governance=governance)
        execution = self._execution(payload.get("execution_evidence"))
        record = self._registry.get(adapter_id, tenant_id=tenant_id, owner_subject=principal_id)
        if record is None:
            raise KeyError("spreadsheet_adapter_not_found")
        bindings = dict(report["bindings"])
        if (
            record.base_model != bindings["base_model_id"]
            or record.artifact_sha256 != bindings["adapter_digest"]
            or record.dataset_hash != governance["dataset_artifact_digest"]
            or "spreadsheet_actions" not in set(record.task_kinds)
            or record.provenance_verified is not True
            or record.source_ids != execution["source_ids"]
            or record.run_ids != execution["run_ids"]
        ):
            raise PermissionError("spreadsheet_adapter_registry_binding_mismatch")
        evaluation_id = str(bindings["evaluation_id"])
        score = float(dict(report["summary"])["adapter"]["score"])
        if record.status == "trained":
            record = self._registry.set_eval_report(
                adapter_id,
                eval_report_ref=evaluation_id,
                eval_score=score,
                tenant_id=tenant_id,
                owner_subject=principal_id,
                expected_version=expected_version,
            )
        elif record.status == "evaluated":
            if record.eval_report_ref != evaluation_id or record.eval_score != score:
                raise PermissionError("spreadsheet_adapter_evaluation_binding_mismatch")
        elif record.status != "approved":
            raise PermissionError("spreadsheet_adapter_status_not_admissible")
        evidence = {
            "dataset_hash": governance["dataset_artifact_digest"],
            "source_ids": execution["source_ids"],
            "run_ids": execution["run_ids"],
            "metrics": {
                "spreadsheet_report_digest": report["report_digest"],
                "spreadsheet_bindings_digest": bindings["bindings_digest"],
                "spreadsheet_governance_digest": governance["governance_digest"],
                "dataset_recipe_digest": governance["dataset_recipe_digest"],
                "split_lock_digest": governance["split_lock_digest"],
                "action_schema_digest": governance["action_schema_digest"],
                "serializer_digest": governance["serializer_digest"],
                "training_policy_digest": governance["policy_digest"],
                "evaluation_policy_digest": bindings["policy_digest"],
                "runtime_digest": bindings["runtime_digest"],
                "engine_digest": bindings["engine_digest"],
                "adapter_score": score,
            },
            "job_id": execution["job_id"],
            "attempt_id": execution["attempt_id"],
            "fencing_token_digest": execution["fencing_token_digest"],
            "base_model_id": bindings["base_model_id"],
            "base_model_sha256": governance["base_model_digest"],
            "adapter_id": adapter_id,
            "adapter_sha256": bindings["adapter_digest"],
            "export_sha256": execution["export_sha256"],
        }
        promoted, replayed = self._registry.promote_evaluated(
            adapter_id,
            artifact_sha256=str(record.artifact_sha256 or ""),
            evaluation_id=evaluation_id,
            evidence=evidence,
            approved_by=principal_id,
            reason=reason,
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            owner_subject=principal_id,
            expected_version=record.registry_version,
            minimum_eval_score=score,
        )
        result = {
            "schema": self.RESULT_SCHEMA,
            "adapter_id": promoted.adapter_id,
            "adapter_version": promoted.version,
            "registry_version": promoted.registry_version,
            "status": promoted.status,
            "evaluation_id": evaluation_id,
            "evaluation_report_digest": report["report_digest"],
            "training_governance_digest": governance["governance_digest"],
            "promotion_evidence_digest": canonical_digest(evidence),
            "replayed": replayed,
            "automatic_apply": False,
            "human_intervention_required": False,
        }
        result["admission_digest"] = canonical_digest(result)
        return result

    @classmethod
    def _governance(cls, value: Any) -> dict[str, str]:
        if not isinstance(value, Mapping) or set(value) != cls.GOVERNANCE_FIELDS:
            raise ValueError("spreadsheet_adapter_governance_fields_invalid")
        normalized = {field: require_digest(value.get(field), field) for field in cls.GOVERNANCE_FIELDS}
        supplied = normalized.pop("governance_digest")
        if canonical_digest(normalized) != supplied:
            raise ValueError("spreadsheet_adapter_governance_digest_mismatch")
        return {**normalized, "governance_digest": supplied}

    @classmethod
    def _report(cls, value: Any, *, adapter_id: str, governance: Mapping[str, str]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("spreadsheet_adapter_evaluation_report_invalid")
        report = dict(value)
        supplied = require_digest(report.pop("report_digest", None), "report_digest")
        if canonical_digest(report) != supplied:
            raise ValueError("spreadsheet_adapter_evaluation_report_digest_mismatch")
        report["report_digest"] = supplied
        bindings = report.get("bindings")
        gates = report.get("gates")
        coverage = report.get("coverage")
        if (
            report.get("schema") != "ananta.spreadsheet-evaluation-report.v2"
            or report.get("mode") != "non_publishing"
            or report.get("adapter_admitted") is not True
            or report.get("reason_codes") != []
            or report.get("published_candidates") != 0
            or report.get("feedback_events") != 0
            or report.get("consent_events") != 0
            or report.get("human_intervention_required") is not False
            or not isinstance(bindings, Mapping)
            or not isinstance(gates, Mapping)
            or not gates
            or any(passed is not True for passed in gates.values())
            or not isinstance(coverage, Mapping)
            or set(coverage) != {"group_dimensions_complete", "expected_diff_complete", "resource_usage_complete"}
            or any(complete is not True for complete in coverage.values())
        ):
            raise PermissionError("spreadsheet_adapter_evaluation_gate_failed")
        expected = {
            "adapter_id": adapter_id,
            "base_model_digest": governance["base_model_digest"],
            "dataset_manifest_digest": governance["dataset_manifest_digest"],
            "dataset_artifact_digest": governance["dataset_artifact_digest"],
            "dataset_recipe_digest": governance["dataset_recipe_digest"],
            "split_lock_digest": governance["split_lock_digest"],
            "training_profile_digest": governance["training_profile_digest"],
            "training_admission_digest": governance["training_admission_digest"],
            "training_governance_digest": governance["governance_digest"],
            "training_policy_digest": governance["policy_digest"],
            "resource_profile_digest": governance["resource_profile_digest"],
            "output_schema_digest": governance["action_schema_digest"],
            "serializer_digest": governance["serializer_digest"],
        }
        if any(bindings.get(field) != expected_value for field, expected_value in expected.items()):
            raise PermissionError("spreadsheet_adapter_evaluation_binding_mismatch")
        binding_digest = bindings.get("bindings_digest")
        bound_values = {key: child for key, child in bindings.items() if key != "bindings_digest"}
        if canonical_digest(bound_values) != binding_digest:
            raise ValueError("spreadsheet_adapter_evaluation_bindings_digest_mismatch")
        for field in ("adapter_digest", "runtime_digest", "engine_digest", "policy_digest"):
            require_digest(bindings.get(field), field)
        return report

    @classmethod
    def _execution(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != cls.EXECUTION_FIELDS:
            raise ValueError("spreadsheet_adapter_execution_evidence_fields_invalid")
        source_ids = list(normalize_source_ids(value.get("source_ids")))
        run_ids = list(normalize_run_ids(value.get("run_ids")))
        if not source_ids or not run_ids:
            raise ValueError("spreadsheet_adapter_execution_evidence_sources_required")
        return {
            "job_id": require_id(value.get("job_id"), "job_id"),
            "attempt_id": require_id(value.get("attempt_id"), "attempt_id"),
            "fencing_token_digest": require_digest(value.get("fencing_token_digest"), "fencing_token_digest"),
            "source_ids": source_ids,
            "run_ids": run_ids,
            "export_sha256": require_digest(value.get("export_sha256"), "export_sha256"),
        }


__all__ = ["SpreadsheetAdapterAdmissionService", "SpreadsheetAdapterRegistryPort"]
