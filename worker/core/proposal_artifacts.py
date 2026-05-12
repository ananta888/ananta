"""Proposal artifact types v1.

Advisory-only artifacts produced by non-executable strategies.
None of these may be directly executed — they require validation, approval
or conversion to ExecutableProposal before any execution path.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

_UNSAFE_PATH_RE = re.compile(r"(?:^|/)\.\./|^/|^[A-Za-z]:[/\\]")


def _validate_relative_path(path: str) -> str:
    """Raise ValueError for absolute paths, path traversal or drive-letter roots."""
    if not path or _UNSAFE_PATH_RE.search(path):
        raise ValueError(f"unsafe_proposal_artifact_path: {path!r}")
    return path


# ── PlannerProposalArtifact ───────────────────────────────────────────────────

@dataclass
class PlannerProposalArtifact:
    """Advisory artifact produced by a planner LLM.

    Stores non-executable plans and advice.  Never authoritative for
    task graph mutation or direct execution.
    """
    artifact_id: str
    task_id: str
    goal_id: str
    source_strategy: str
    source_model: str | None = None
    raw_text_ref: str | None = None
    parse_status: str = "unparsed"
    parse_error: str | None = None
    parsed_items: list[dict[str, Any]] = field(default_factory=list)
    confidence: float | None = None
    validation_errors: list[str] = field(default_factory=list)
    adoption_status: str = "pending"
    adoption_reason: str | None = None
    created_at: float = field(default_factory=time.time)

    _VALID_PARSE_STATUSES = frozenset({
        "parsed", "failed", "unparsed", "malformed_json",
        "markdown_fenced", "natural_language",
    })
    _VALID_ADOPTION_STATUSES = frozenset({"pending", "adopted", "rejected", "ignored"})

    def __post_init__(self) -> None:
        if self.parse_status not in self._VALID_PARSE_STATUSES:
            raise ValueError(f"invalid_parse_status: {self.parse_status!r}")
        if self.adoption_status not in self._VALID_ADOPTION_STATUSES:
            raise ValueError(f"invalid_adoption_status: {self.adoption_status!r}")

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        goal_id: str,
        source_strategy: str,
        source_model: str | None = None,
        raw_text_ref: str | None = None,
        parse_status: str = "unparsed",
        parsed_items: list[dict[str, Any]] | None = None,
        confidence: float | None = None,
    ) -> "PlannerProposalArtifact":
        return cls(
            artifact_id=f"ppa-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            goal_id=goal_id,
            source_strategy=source_strategy,
            source_model=source_model,
            raw_text_ref=raw_text_ref,
            parse_status=parse_status,
            parsed_items=list(parsed_items or []),
            confidence=confidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "planner_proposal_artifact.v1",
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "source_strategy": self.source_strategy,
            "source_model": self.source_model,
            "raw_text_ref": self.raw_text_ref,
            "parse_status": self.parse_status,
            "parse_error": self.parse_error,
            "parsed_items": list(self.parsed_items),
            "confidence": self.confidence,
            "validation_errors": list(self.validation_errors),
            "adoption_status": self.adoption_status,
            "adoption_reason": self.adoption_reason,
            "created_at": self.created_at,
        }


# ── FileProposalArtifact ──────────────────────────────────────────────────────

@dataclass
class FileProposalArtifact:
    """Proposed file write.

    Requires validation and optional approval before apply.
    Absolute paths and path traversal are rejected at construction time.
    """
    artifact_id: str
    task_id: str
    goal_id: str
    source_strategy: str
    relative_path: str
    content_ref: str
    content_hash: str | None = None
    encoding: str = "utf-8"
    operation: str = "create"
    source_model: str | None = None
    confidence: float | None = None
    validation_errors: list[str] = field(default_factory=list)
    safety_warnings: list[str] = field(default_factory=list)
    adoption_status: str = "pending"
    created_at: float = field(default_factory=time.time)

    _VALID_OPERATIONS = frozenset({"create", "overwrite", "append", "delete"})
    _VALID_ADOPTION = frozenset({"pending", "adopted", "rejected", "ignored"})

    def __post_init__(self) -> None:
        _validate_relative_path(self.relative_path)
        if self.operation not in self._VALID_OPERATIONS:
            raise ValueError(f"invalid_file_proposal_operation: {self.operation!r}")
        if self.adoption_status not in self._VALID_ADOPTION:
            raise ValueError(f"invalid_adoption_status: {self.adoption_status!r}")

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        goal_id: str,
        source_strategy: str,
        relative_path: str,
        content_ref: str,
        content_hash: str | None = None,
        operation: str = "create",
        source_model: str | None = None,
        confidence: float | None = None,
    ) -> "FileProposalArtifact":
        return cls(
            artifact_id=f"fpa-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            goal_id=goal_id,
            source_strategy=source_strategy,
            relative_path=_validate_relative_path(relative_path),
            content_ref=content_ref,
            content_hash=content_hash,
            operation=operation,
            source_model=source_model,
            confidence=confidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "file_proposal_artifact.v1",
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "source_strategy": self.source_strategy,
            "relative_path": self.relative_path,
            "content_ref": self.content_ref,
            "content_hash": self.content_hash,
            "encoding": self.encoding,
            "operation": self.operation,
            "source_model": self.source_model,
            "confidence": self.confidence,
            "validation_errors": list(self.validation_errors),
            "safety_warnings": list(self.safety_warnings),
            "adoption_status": self.adoption_status,
            "created_at": self.created_at,
        }


# ── PatchProposalArtifact ─────────────────────────────────────────────────────

@dataclass
class PatchProposalArtifact:
    """Unified diff/patch proposal.

    Requires validation and optional approval before apply.
    Target paths are validated; absolute paths and traversal are rejected.
    """
    artifact_id: str
    task_id: str
    goal_id: str
    source_strategy: str
    target_paths: list[str]
    patch_ref: str
    patch_hash: str | None = None
    source_model: str | None = None
    confidence: float | None = None
    validation_errors: list[str] = field(default_factory=list)
    safety_warnings: list[str] = field(default_factory=list)
    adoption_status: str = "pending"
    lines_added: int | None = None
    lines_removed: int | None = None
    created_at: float = field(default_factory=time.time)

    _VALID_ADOPTION = frozenset({"pending", "adopted", "rejected", "ignored"})

    def __post_init__(self) -> None:
        for p in self.target_paths:
            _validate_relative_path(p)
        if self.adoption_status not in self._VALID_ADOPTION:
            raise ValueError(f"invalid_adoption_status: {self.adoption_status!r}")

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        goal_id: str,
        source_strategy: str,
        target_paths: list[str],
        patch_ref: str,
        patch_hash: str | None = None,
        source_model: str | None = None,
        confidence: float | None = None,
    ) -> "PatchProposalArtifact":
        validated_paths = [_validate_relative_path(p) for p in target_paths]
        return cls(
            artifact_id=f"ppa-patch-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            goal_id=goal_id,
            source_strategy=source_strategy,
            target_paths=validated_paths,
            patch_ref=patch_ref,
            patch_hash=patch_hash,
            source_model=source_model,
            confidence=confidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "patch_proposal_artifact.v1",
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "source_strategy": self.source_strategy,
            "target_paths": list(self.target_paths),
            "patch_ref": self.patch_ref,
            "patch_hash": self.patch_hash,
            "source_model": self.source_model,
            "confidence": self.confidence,
            "validation_errors": list(self.validation_errors),
            "safety_warnings": list(self.safety_warnings),
            "adoption_status": self.adoption_status,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "created_at": self.created_at,
        }


# ── AdvisoryProposalArtifact ──────────────────────────────────────────────────

@dataclass
class AdvisoryProposalArtifact:
    """Plain natural language output — never executable."""
    artifact_id: str
    task_id: str
    goal_id: str
    source_strategy: str
    text: str
    source_model: str | None = None
    source_format: str = "natural_language"
    confidence: float | None = None
    created_at: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        goal_id: str,
        source_strategy: str,
        text: str,
        source_model: str | None = None,
        source_format: str = "natural_language",
        confidence: float | None = None,
    ) -> "AdvisoryProposalArtifact":
        return cls(
            artifact_id=f"adv-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            goal_id=goal_id,
            source_strategy=source_strategy,
            text=text,
            source_model=source_model,
            source_format=source_format,
            confidence=confidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "advisory_proposal_artifact.v1",
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "source_strategy": self.source_strategy,
            "text": self.text,
            "source_model": self.source_model,
            "source_format": self.source_format,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }
