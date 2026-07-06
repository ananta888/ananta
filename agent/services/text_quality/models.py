from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContentKind(str, Enum):
    FREEFORM_PROSE = "freeform_prose"
    PLANNING_TASK_DESCRIPTION = "planning_task_description"
    TECHNICAL_DOCUMENTATION = "technical_documentation"
    CHANGELOG = "changelog"
    COURSE_MATERIAL = "course_material"
    STRUCTURED_PLAN = "structured_plan"


class EvaluationStatus(str, Enum):
    COMPLETED = "completed"
    DEGRADED = "degraded"
    UNSCORABLE = "unscorable"
    FAILED = "failed"


KNOWN_REASON_CODES = frozenset(
    {
        "generic_phrase",
        "shallow_claim",
        "missing_concrete_example",
        "unsupported_specific_claim",
        "vague_attribution",
        "overused_transition",
        "structure_mismatch",
        "style_uniformity",
        "source_unverified",
        "text_too_short",
        "text_too_long",
        "provider_unavailable",
        "provider_timeout",
        "provider_invalid_response",
        "provider_checksum_mismatch",
        "upstream_unknown",
        "llm_judge_failed",
    }
)


def _score(value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("score_must_be_finite")
    return max(0.0, min(1.0, number))


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    excerpt: str = Field(max_length=160)
    severity: str = "medium"

    @field_validator("reason_code")
    @classmethod
    def known_reason(cls, value: str) -> str:
        if value not in KNOWN_REASON_CODES:
            raise ValueError("unknown_reason_code")
        return value


class DetectorSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_name: str
    provider_version: str
    raw_signal_score: float = 0.0
    normalized_signal_score: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    confidence: float = 0.0
    status: EvaluationStatus = EvaluationStatus.COMPLETED
    degraded_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _normalize_raw = field_validator("normalized_signal_score", "confidence")(_score)

    @field_validator("reason_codes")
    @classmethod
    def known_reasons(cls, values: list[str]) -> list[str]:
        unknown = set(values) - KNOWN_REASON_CODES
        if unknown:
            raise ValueError(f"unknown_reason_code:{sorted(unknown)[0]}")
        return list(dict.fromkeys(values))


class CriteriaSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: f"criteria-{uuid.uuid4().hex}")
    version: str = "1.0"
    language: str = "de"
    profile_name: str = "critical_editor_de"
    content_kinds: list[ContentKind]
    status: str = "proposed"
    blocked_phrases: list[str] = Field(default_factory=list)
    weak_patterns: list[dict[str, Any]] = Field(default_factory=list)
    required_positive_traits: list[str] = Field(default_factory=list)
    criterion_confidence: dict[str, float] = Field(default_factory=dict)
    requires_review: bool = True
    thresholds: dict[str, float] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_by: str = "system"
    created_at: float = Field(default_factory=time.time)
    checksum: str = ""

    def canonical_checksum(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"id", "status", "created_by", "created_at", "checksum"},
        )
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class TextQualityEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    language: str = "de"
    content_kind: ContentKind = ContentKind.FREEFORM_PROSE
    criteria: CriteriaSet
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    max_input_chars: int = Field(default=12000, ge=1, le=100000)
    max_input_words: int = Field(default=2500, ge=1, le=20000)


class TextQualityEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_id: str = Field(default_factory=lambda: f"tqe-{uuid.uuid4().hex}")
    slop_score: float
    depth_score: float
    style_fit_score: float
    reason_codes: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    source_breakdown: list[DetectorSignal] = Field(default_factory=list)
    grounding_status: str = "not_required"
    evaluator_version: str = "text-quality-v1"
    criteria_version: str
    language: str
    content_kind: ContentKind
    confidence: float
    status: EvaluationStatus
    improvement_hints: list[str] = Field(default_factory=list)

    _normalize_scores = field_validator("slop_score", "depth_score", "style_fit_score", "confidence")(_score)

    @field_validator("reason_codes")
    @classmethod
    def known_reasons(cls, values: list[str]) -> list[str]:
        unknown = set(values) - KNOWN_REASON_CODES
        if unknown:
            raise ValueError(f"unknown_reason_code:{sorted(unknown)[0]}")
        return list(dict.fromkeys(values))
