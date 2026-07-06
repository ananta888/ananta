from __future__ import annotations

import time
import uuid

from sqlmodel import JSON, Column, Field, SQLModel


class TextQualityCriteriaSetDB(SQLModel, table=True):
    __tablename__ = "text_quality_criteria_sets"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    version: str = Field(index=True)
    language: str = Field(index=True)
    profile_name: str = Field(index=True)
    content_kinds: list[str] = Field(default=[], sa_column=Column(JSON))
    status: str = Field(default="proposed", index=True)
    criteria_payload: dict = Field(default={}, sa_column=Column(JSON))
    checksum: str = Field(index=True, unique=True)
    source_refs: list[dict] = Field(default=[], sa_column=Column(JSON))
    created_by: str = "system"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class TextQualityEvaluationDB(SQLModel, table=True):
    __tablename__ = "text_quality_evaluations"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    planning_run_id: str | None = Field(default=None, index=True)
    planning_evaluation_id: str | None = Field(default=None, index=True)
    criteria_set_id: str | None = Field(default=None, index=True)
    prompt_version_id: str | None = Field(default=None, index=True)
    evaluator_version: str = Field(index=True)
    criteria_version: str = Field(index=True)
    language: str = Field(index=True)
    content_kind: str = Field(index=True)
    status: str = Field(index=True)
    slop_score: float = 0.0
    depth_score: float = 0.0
    style_fit_score: float = 0.0
    confidence: float = 0.0
    reason_codes: list[str] = Field(default=[], sa_column=Column(JSON))
    result_payload: dict = Field(default={}, sa_column=Column(JSON))
    identity_checksum: str = Field(index=True, unique=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
