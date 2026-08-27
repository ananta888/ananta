"""Closed contracts for governed local tool-learning records and snapshots."""

from __future__ import annotations

import re
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ToolDecision(_Closed):
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_name")
    @classmethod
    def _tool_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if not _ID.fullmatch(normalized):
            raise ValueError("tool_training_tool_name_invalid")
        return normalized


class IndependentToolOutcome(_Closed):
    schema_version: Literal["ananta.independent-tool-outcome.v1"] = "ananta.independent-tool-outcome.v1"
    interaction_id: str
    decision: ToolDecision
    outcome_source: Literal["authorized_execution", "human_review", "golden_fixture"]
    execution_status: Literal[
        "completed", "schema_rejected", "tool_rejected", "arguments_rejected", "timed_out", "failed"
    ]
    evidence_sha256: str

    @field_validator("interaction_id")
    @classmethod
    def _interaction_id(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not _ID.fullmatch(normalized):
            raise ValueError("tool_training_identifier_invalid")
        return normalized

    @field_validator("evidence_sha256")
    @classmethod
    def _evidence_digest(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not _SHA256.fullmatch(normalized):
            raise ValueError("tool_training_digest_invalid")
        return normalized


class ToolInteractionTrainingRecord(_Closed):
    schema_version: Literal["ananta.local-tool-training-record.v1"] = "ananta.local-tool-training-record.v1"
    interaction_id: str
    observed_at: str = Field(min_length=20, max_length=64)
    request_class: str
    expected_schema_sha256: str
    candidate: ToolDecision
    independent_outcome: ToolDecision
    outcome_source: Literal["authorized_execution", "human_review", "golden_fixture"]
    outcome_label: Literal[
        "success",
        "schema_error",
        "unknown_tool",
        "invalid_arguments",
        "timeout",
        "execution_error",
    ]
    execution_status: Literal[
        "completed", "schema_rejected", "tool_rejected", "arguments_rejected", "timed_out", "failed"
    ]
    similarity_group_sha256: str
    collector_policy_sha256: str
    redaction_policy_sha256: str
    outcome_evidence_sha256: str

    @field_validator("interaction_id", "request_class")
    @classmethod
    def _identifier(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not _ID.fullmatch(normalized):
            raise ValueError("tool_training_identifier_invalid")
        return normalized

    @field_validator(
        "expected_schema_sha256",
        "similarity_group_sha256",
        "collector_policy_sha256",
        "redaction_policy_sha256",
        "outcome_evidence_sha256",
    )
    @classmethod
    def _digest(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not _SHA256.fullmatch(normalized):
            raise ValueError("tool_training_digest_invalid")
        return normalized

    @model_validator(mode="after")
    def _independent_ground_truth(self) -> "ToolInteractionTrainingRecord":
        if (
            self.outcome_source == "authorized_execution"
            and self.execution_status != "completed"
            and self.outcome_label == "success"
        ):
            raise ValueError("tool_training_success_requires_completed_execution")
        return self

    def to_wire(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.model_dump(mode="json"))


class ToolTrainingDatasetSnapshot(_Closed):
    schema_version: Literal["ananta.local-tool-training-snapshot.v1"] = "ananta.local-tool-training-snapshot.v1"
    snapshot_id: str
    dataset_id: str
    created_at: str = Field(min_length=20, max_length=64)
    train_end: str = Field(min_length=20, max_length=64)
    validation_end: str = Field(min_length=20, max_length=64)
    test_end: str = Field(min_length=20, max_length=64)
    source_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    collector_policy_sha256: str
    redaction_policy_sha256: str
    train_sha256: str
    validation_sha256: str
    test_sha256: str
    manifest_sha256: str
    train_records: int = Field(ge=1)
    validation_records: int = Field(ge=1)
    test_records: int = Field(ge=1)
    verification_status: Literal["verified"] = "verified"

    @field_validator("snapshot_id", "dataset_id")
    @classmethod
    def _snapshot_identifier(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not _ID.fullmatch(normalized):
            raise ValueError("tool_training_snapshot_identifier_invalid")
        return normalized

    @field_validator(
        "collector_policy_sha256",
        "redaction_policy_sha256",
        "train_sha256",
        "validation_sha256",
        "test_sha256",
        "manifest_sha256",
    )
    @classmethod
    def _snapshot_digest(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not _SHA256.fullmatch(normalized):
            raise ValueError("tool_training_digest_invalid")
        return normalized

    def to_wire(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.model_dump(mode="json"))


__all__ = [
    "IndependentToolOutcome",
    "ToolDecision",
    "ToolInteractionTrainingRecord",
    "ToolTrainingDatasetSnapshot",
]
