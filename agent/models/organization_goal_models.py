"""Closed contracts for Organization-scoped Goal intake."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _ClosedContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class OrganizationGoalCreateCommand(_ClosedContract):
    """Caller-controlled intent for one passive Organization root Goal."""

    goal: str = Field(min_length=1, max_length=4096)
    summary: str | None = Field(default=None, max_length=512)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_optional_summary(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("constraints", "acceptance_criteria")
    @classmethod
    def validate_bounded_text_items(cls, values: list[str]) -> list[str]:
        normalized = [str(value or "").strip() for value in values]
        if any(not value or len(value) > 1000 for value in normalized):
            raise ValueError("organization_goal_text_item_invalid")
        if len(set(normalized)) != len(normalized):
            raise ValueError("organization_goal_text_item_duplicate")
        return normalized


class OrganizationGoalCreateResult(_ClosedContract):
    goal_id: str
    trace_id: str
    organization_id: str
    status: Literal["received"]
    goal_kind: Literal["organization"]
    replayed: bool = False


__all__ = ["OrganizationGoalCreateCommand", "OrganizationGoalCreateResult"]
