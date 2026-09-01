"""Spreadsheet-specific projection, scoring and inference contract strategy."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ananta_contracts.spreadsheet_studio import SpreadsheetContractError, canonical_digest, validate_action


class TrainingTaskFamilyStrategy(Protocol):
    @property
    def family(self) -> str: ...

    def validate_record(self, record: Mapping[str, Any]) -> dict[str, Any]: ...

    def score_output(self, output: str) -> dict[str, Any]: ...

    def parse_inference(self, output: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SpreadsheetTrainingTaskFamilyStrategy:
    family: str = "spreadsheet_actions"
    output_schema: str = "ananta.spreadsheet-action-output.v1"

    def validate_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "instruction",
            "input",
            "output",
            "task_kind",
            "privacy_class",
            "quality_label",
            "source_document_version",
            "record_digest",
            "feedback_id",
            "consent_id",
            "consent_digest",
            "lineage_root_id",
            "split",
            "recipe_version",
        }
        if set(record) != required or record.get("task_kind") != self.family:
            raise ValueError("spreadsheet_training_record_fields_invalid")
        if record.get("privacy_class") != "consented_masked" or record.get("split") not in {
            "train",
            "validation",
            "eval",
            "test",
        }:
            raise ValueError("spreadsheet_training_record_policy_invalid")
        for field in ("record_digest", "consent_digest"):
            value = str(record.get(field) or "")
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"spreadsheet_training_{field}_invalid")
        parsed = self.parse_inference(str(record.get("output") or ""))
        normalized = dict(record)
        normalized["output"] = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return normalized

    def score_output(self, output: str) -> dict[str, Any]:
        try:
            parsed = self.parse_inference(output)
        except ValueError as exc:
            return {
                "schema_valid": False,
                "action_valid": False,
                "safe_rejection": False,
                "total": 0.0,
                "reason_code": str(exc),
            }
        refusal = parsed.get("schema") == "ananta.spreadsheet-action-refusal.v1"
        return {
            "schema_valid": True,
            "action_valid": not refusal,
            "safe_rejection": refusal,
            "total": 1.0,
            "reason_code": parsed.get("reason_code") if refusal else None,
        }

    def parse_inference(self, output: str) -> dict[str, Any]:
        if not 1 <= len(output.encode()) <= 1_048_576:
            raise ValueError("spreadsheet_inference_output_size_invalid")
        try:
            value = json.loads(output)
        except (UnicodeError, ValueError) as exc:
            raise ValueError("spreadsheet_inference_json_invalid") from exc
        if not isinstance(value, Mapping):
            raise ValueError("spreadsheet_inference_object_required")
        if value.get("schema") == "ananta.spreadsheet-action-refusal.v1":
            if set(value) != {"schema", "reason_code"}:
                raise ValueError("spreadsheet_inference_refusal_fields_invalid")
            reason = str(value.get("reason_code") or "")
            if not reason.startswith("spreadsheet_") or len(reason) > 128:
                raise ValueError("spreadsheet_inference_refusal_reason_invalid")
            return {"schema": "ananta.spreadsheet-action-refusal.v1", "reason_code": reason}
        if set(value) != {"schema", "actions"} or value.get("schema") != self.output_schema:
            raise ValueError("spreadsheet_inference_fields_invalid")
        actions = value.get("actions")
        if not isinstance(actions, list) or not 1 <= len(actions) <= 1_000:
            raise ValueError("spreadsheet_inference_actions_invalid")
        try:
            normalized = [validate_action(action) for action in actions]
        except SpreadsheetContractError as exc:
            raise ValueError(str(exc)) from exc
        return {"schema": self.output_schema, "actions": normalized}

    @property
    def schema_digest(self) -> str:
        return canonical_digest(
            {
                "family": self.family,
                "output_schema": self.output_schema,
                "action_kinds": ["clear_cell", "set_formula", "set_value"],
            }
        )

    @property
    def serializer_digest(self) -> str:
        return canonical_digest(
            {
                "serializer": "canonical-json",
                "version": "spreadsheet-action-json.v1",
                "ensure_ascii": False,
                "sort_keys": True,
                "separators": [",", ":"],
            }
        )


class TrainingTaskFamilyRegistry:
    def __init__(self, strategies: tuple[TrainingTaskFamilyStrategy, ...] = ()) -> None:
        configured = strategies or (SpreadsheetTrainingTaskFamilyStrategy(),)
        self._strategies = {strategy.family: strategy for strategy in configured}
        if len(self._strategies) != len(configured):
            raise ValueError("training_task_family_duplicate")

    def require(self, family: str) -> TrainingTaskFamilyStrategy:
        try:
            return self._strategies[family]
        except KeyError as exc:
            raise ValueError("training_task_family_unknown") from exc


__all__ = [
    "SpreadsheetTrainingTaskFamilyStrategy",
    "TrainingTaskFamilyRegistry",
    "TrainingTaskFamilyStrategy",
]
