"""Closed contract for maintainer-authored local-adapter evaluation fixtures."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REQUIRED_EVALUATION_SLICES = frozenset({"golden", "ood", "abstain", "injection", "malformed_schema"})
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CuratedEvaluationProvenance(_Closed):
    authoring_source: Literal["maintainer_authored_contract"]
    candidate_generated: Literal[False]
    training_eligible: Literal[False]
    verification_status: Literal["unverified_no_source_ids"]
    source_ids: tuple[()] = ()
    run_ids: tuple[()] = ()


class CuratedSliceThreshold(_Closed):
    minimum_accuracy: float = Field(ge=0.0, le=1.0)
    maximum_regression: float = Field(ge=0.0, le=1.0)


class CuratedToolEvaluationCase(_Closed):
    case_id: str
    slice_id: Literal[
        "golden",
        "ood",
        "abstain",
        "injection",
        "malformed_schema",
    ]
    request: str = Field(min_length=1, max_length=1_000)
    allowed_tools: tuple[str, ...]
    expected_tool: str | None
    required_argument_types: Mapping[
        str,
        Literal["string", "integer", "number", "boolean"],
    ]
    expected_arguments: Mapping[str, Any]
    provenance: CuratedEvaluationProvenance

    @field_validator("case_id")
    @classmethod
    def _case_id(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if _IDENTIFIER.fullmatch(normalized) is None:
            raise ValueError("local_adapter_fixture_case_id_invalid")
        return normalized

    @field_validator("allowed_tools")
    @classmethod
    def _allowed_tools(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(str(value).strip().lower() for value in values)
        if (
            not normalized
            or len(set(normalized)) != len(normalized)
            or any(_IDENTIFIER.fullmatch(value) is None for value in normalized)
        ):
            raise ValueError("local_adapter_fixture_allowed_tools_invalid")
        return normalized

    @field_validator("expected_tool")
    @classmethod
    def _expected_tool(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if _IDENTIFIER.fullmatch(normalized) is None:
            raise ValueError("local_adapter_fixture_expected_tool_invalid")
        return normalized

    @model_validator(mode="after")
    def _expected_decision(self) -> "CuratedToolEvaluationCase":
        if self.expected_tool is not None and self.expected_tool not in self.allowed_tools:
            raise ValueError("local_adapter_fixture_expected_tool_not_allowed")
        expected_keys = set(self.expected_arguments)
        if expected_keys != set(self.required_argument_types):
            raise ValueError("local_adapter_fixture_argument_contract_mismatch")
        for key in expected_keys:
            if _IDENTIFIER.fullmatch(str(key).strip().lower()) is None:
                raise ValueError("local_adapter_fixture_argument_name_invalid")
            if not _matches_type(
                self.expected_arguments[key],
                self.required_argument_types[key],
            ):
                raise ValueError("local_adapter_fixture_argument_type_mismatch")
        if self.expected_tool is None and expected_keys:
            raise ValueError("local_adapter_fixture_abstain_arguments_forbidden")
        if self.slice_id in {"ood", "abstain", "injection", "malformed_schema"} and self.expected_tool is not None:
            raise ValueError("local_adapter_fixture_slice_must_abstain")
        return self


class CuratedLocalAdapterEvaluationFixture(_Closed):
    schema_version: Literal["ananta.local-adapter-curated-evaluation.v1"] = "ananta.local-adapter-curated-evaluation.v1"
    fixture_id: str
    evaluation_seed: int = Field(ge=0, le=2**32 - 1)
    thresholds: Mapping[str, CuratedSliceThreshold]
    cases: tuple[CuratedToolEvaluationCase, ...]

    @field_validator("fixture_id")
    @classmethod
    def _fixture_id(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if _IDENTIFIER.fullmatch(normalized) is None:
            raise ValueError("local_adapter_fixture_id_invalid")
        return normalized

    @model_validator(mode="after")
    def _complete_slices(self) -> "CuratedLocalAdapterEvaluationFixture":
        if set(self.thresholds) != REQUIRED_EVALUATION_SLICES:
            raise ValueError("local_adapter_fixture_threshold_slices_invalid")
        if not self.cases or len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("local_adapter_fixture_cases_invalid")
        observed = {case.slice_id for case in self.cases}
        if observed != REQUIRED_EVALUATION_SLICES:
            raise ValueError("local_adapter_fixture_case_slices_invalid")
        return self

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "CuratedLocalAdapterEvaluationFixture":
        return cls.model_validate(dict(value))

    @property
    def sha256(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


__all__ = [
    "CuratedLocalAdapterEvaluationFixture",
    "CuratedSliceThreshold",
    "CuratedToolEvaluationCase",
    "REQUIRED_EVALUATION_SLICES",
]
