"""Digest-bound deterministic spreadsheet validation orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.spreadsheet_validation_reference_port import SpreadsheetValidationReferenceRepositoryPort
from agent.services.spreadsheet_validation_rule_evaluator import SpreadsheetValidationRuleEvaluator
from ananta_contracts.spreadsheet_studio import WorkbookSnapshotV1, canonical_digest, require_digest
from ananta_contracts.spreadsheet_studio_v2 import parse_workbook_snapshot


class SpreadsheetValidatorEngine:
    def __init__(
        self,
        reference_repository: SpreadsheetValidationReferenceRepositoryPort | None = None,
        *,
        evaluator: SpreadsheetValidationRuleEvaluator | None = None,
    ) -> None:
        self._evaluator = evaluator or SpreadsheetValidationRuleEvaluator(reference_repository)

    def validate(
        self,
        snapshot: Mapping[str, Any] | WorkbookSnapshotV1,
        validators: Sequence[Mapping[str, Any]],
        *,
        tenant_id: str | None = None,
        actual_diff: Mapping[str, Any] | None = None,
        bindings: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate = snapshot.to_dict() if isinstance(snapshot, WorkbookSnapshotV1) else dict(snapshot)
        parsed = parse_workbook_snapshot(candidate)
        normalized_validators = [dict(validator) for validator in validators]
        results = self._evaluator.evaluate(
            snapshot=parsed.to_dict(),
            validators=normalized_validators,
            tenant_id=tenant_id,
            actual_diff=actual_diff,
        )
        supplied_bindings = dict(bindings or {})
        result_bindings = {
            "document_digest": _binding_digest(supplied_bindings, "document_digest", candidate),
            "candidate_digest": parsed.digest,
            "task_digest": _binding_digest(supplied_bindings, "task_digest", normalized_validators),
            "engine_digest": _binding_digest(supplied_bindings, "engine_digest", {"engine": "unspecified"}),
            "recalc_digest": _binding_digest(supplied_bindings, "recalc_digest", _recalc_profile(candidate)),
            "policy_digest": _binding_digest(supplied_bindings, "policy_digest", {"policy": "unspecified"}),
            "validator_spec_digest": canonical_digest(normalized_validators),
        }
        unsafe = bool(candidate.get("unsupported_objects"))
        not_verifiable = any(item["state"] == "not_verifiable" for item in results)
        failures = [item for item in results if not item["passed"]]
        unexpected = any(item["reason_code"] == "spreadsheet_validator_unexpected_change" for item in failures)
        changed = bool(actual_diff and actual_diff.get("total"))
        passed = not failures and not unsafe
        correctness = (
            "not_verifiable"
            if not_verifiable
            else "correct"
            if not failures
            else "partially_correct"
            if len(failures) < len(results)
            else "incorrect"
        )
        outcome = (
            "unsafe"
            if unsafe
            else "not_verifiable"
            if not_verifiable
            else "unexpectedly_changed"
            if unexpected
            else "unchanged"
            if not changed
            else correctness
        )
        reasons = sorted(
            {
                *[str(item["reason_code"]) for item in failures if item["reason_code"]],
                *(["spreadsheet_candidate_unsafe"] if unsafe else []),
            }
        )
        value = {
            "schema": "ananta.spreadsheet-validation-result.v2",
            "passed": passed,
            "technically_valid": True,
            "correctness": correctness,
            "change_classification": "unchanged" if not changed else "unexpected" if unexpected else "expected",
            "safety": "unsafe" if unsafe else "safe",
            "outcome": outcome,
            "bindings": result_bindings,
            "results": results,
            "reason_codes": reasons,
            "source_grounding_verified": False,
            "human_intervention_required": False,
        }
        value["validation_digest"] = canonical_digest(value)
        return value


def _binding_digest(bindings: Mapping[str, Any], field: str, fallback: Any) -> str:
    supplied = bindings.get(field)
    return require_digest(supplied, field) if supplied is not None else canonical_digest(fallback)


def _recalc_profile(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "locale": snapshot.get("locale", "und"),
        "timezone": snapshot.get("timezone", "UTC"),
        "date_system": snapshot.get("date_system", "1900"),
        "recalc_profile": snapshot.get("recalc_profile", "automatic"),
    }


__all__ = ["SpreadsheetValidatorEngine"]
