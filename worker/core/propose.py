"""Propose contracts v1 — ExecutableProposal and ProposeStrategyResult.

Only ExecutableProposal may be passed to the execute step.  All other
ProposeStrategyResult kinds are advisory or terminal and must not become
runnable control state.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from abc import ABC, abstractmethod


# ── ProposeStrategyResult status codes ────────────────────────────────────────

STATUS_EXECUTABLE = "executable"
STATUS_DECLINED = "declined"
STATUS_ADVISORY = "advisory"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_FAILED = "failed"
STATUS_POLICY_DENIED = "policy_denied"

_TERMINAL_STATUSES = {STATUS_FAILED, STATUS_POLICY_DENIED, STATUS_NEEDS_REVIEW}
_EXECUTABLE_ONLY = {STATUS_EXECUTABLE}


# ── ProposalBase ────────────────────────────────────────────────────────

class ProposalBase(ABC):
    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

# ── ExecutableProposal ────────────────────────────────────────────────────────

@dataclass
class ExecutableProposal(ProposalBase):
    """Normalized, validated proposal that may be passed to the execute step.

    Invariant: at least one of ``command`` or ``tool_calls`` must be present.
    """
    proposal_id: str
    goal_id: str
    task_id: str
    strategy_id: str
    command: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    expected_artifacts: list[dict[str, Any]] = field(default_factory=list)
    safety_flags: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command and not self.tool_calls:
            raise ValueError("executable_proposal_requires_command_or_tool_calls")
        if self.command is not None and not isinstance(self.command, str):
            raise TypeError("command_must_be_str")
        if not isinstance(self.tool_calls, list):
            raise TypeError("tool_calls_must_be_list")

    @classmethod
    def from_command(
        cls,
        *,
        goal_id: str,
        task_id: str,
        strategy_id: str,
        command: str,
        reason: str | None = None,
        expected_artifacts: list[dict[str, Any]] | None = None,
        safety_flags: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ExecutableProposal":
        return cls(
            proposal_id=f"prop-{uuid.uuid4().hex[:12]}",
            goal_id=goal_id,
            task_id=task_id,
            strategy_id=strategy_id,
            command=command,
            tool_calls=[],
            expected_artifacts=list(expected_artifacts or []),
            safety_flags=dict(safety_flags or {}),
            reason=reason,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_tool_calls(
        cls,
        *,
        goal_id: str,
        task_id: str,
        strategy_id: str,
        tool_calls: list[dict[str, Any]],
        required_tools: list[str] | None = None,
        reason: str | None = None,
        expected_artifacts: list[dict[str, Any]] | None = None,
        safety_flags: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ExecutableProposal":
        if not tool_calls:
            raise ValueError("tool_calls_must_be_non_empty")
        return cls(
            proposal_id=f"prop-{uuid.uuid4().hex[:12]}",
            goal_id=goal_id,
            task_id=task_id,
            strategy_id=strategy_id,
            command=None,
            tool_calls=list(tool_calls),
            required_tools=list(required_tools or []),
            expected_artifacts=list(expected_artifacts or []),
            safety_flags=dict(safety_flags or {}),
            reason=reason,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "executable_proposal.v1",
            "proposal_id": self.proposal_id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "strategy_id": self.strategy_id,
            "command": self.command,
            "tool_calls": list(self.tool_calls),
            "required_tools": list(self.required_tools),
            "expected_artifacts": list(self.expected_artifacts),
            "safety_flags": dict(self.safety_flags),
            "reason": self.reason,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


class ProposalBase(ABC):
    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

@dataclass
class AdvisoryProposalArtifact(ProposalBase):
    proposal_id: str
    goal_id: str
    task_id: str
    strategy_id: str
    advisory_text: str | None = None
    advisory_artifact_ref: str | None = None
    reason: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.advisory_text is None and self.advisory_artifact_ref is None:
            raise ValueError("advisory_proposal_artifact_must_have_text_or_artifact_ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "advisory_proposal_artifact.v1",
            "proposal_id": self.proposal_id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "strategy_id": self.strategy_id,
            "advisory_text": self.advisory_text,
            "advisory_artifact_ref": self.advisory_artifact_ref,
            "reason": self.reason,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

@dataclass
class PatchProposalArtifact(ProposalBase):
    proposal_id: str
    goal_id: str
    task_id: str
    strategy_id: str
    patches: list[dict[str, Any]] = field(default_factory=list)
    expected_artifacts: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.patches:
            raise ValueError("patch_proposal_artifact_patches_must_be_non_empty")
        for patch in self.patches:
            if "path" not in patch or "content" not in patch:
                raise ValueError("each_patch_must_have_path_and_content")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "patch_proposal_artifact.v1",
            "proposal_id": self.proposal_id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "strategy_id": self.strategy_id,
            "patches": list(self.patches),
            "expected_artifacts": list(self.expected_artifacts),
            "reason": self.reason,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

@dataclass
class FileProposalArtifact(ProposalBase):
    proposal_id: str
    goal_id: str
    task_id: str
    strategy_id: str
    files: list[dict[str, Any]] = field(default_factory=list)
    expected_artifacts: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.files:
            raise ValueError("file_proposal_artifact_files_must_be_non_empty")
        for f in self.files:
            if "path" not in f or "content" not in f:
                raise ValueError("each_file_must_have_path_and_content")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "file_proposal_artifact.v1",
            "proposal_id": self.proposal_id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "strategy_id": self.strategy_id,
            "files": list(self.files),
            "expected_artifacts": list(self.expected_artifacts),
            "reason": self.reason,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

@dataclass
class PlannerProposalArtifact(ProposalBase):
    proposal_id: str
    goal_id: str
    task_id: str
    strategy_id: str
    sub_tasks: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.sub_tasks:
            raise ValueError("planner_proposal_artifact_sub_tasks_must_be_non_empty")
        for st in self.sub_tasks:
            if not all(k in st for k in ("task_id", "title", "description", "kind")):
                raise ValueError("each_sub_task_must_have_task_id_title_description_kind")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "planner_proposal_artifact.v1",
            "proposal_id": self.proposal_id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "strategy_id": self.strategy_id,
            "sub_tasks": list(self.sub_tasks),
            "reason": self.reason,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

# ── ProposeStrategyResult ─────────────────────────────────────────────────────

@dataclass
class ProposeStrategyResult:
    """Result of one strategy's attempt to produce a proposal.

    Only ``status == 'executable'`` may carry a non-None ``proposal``.
    Free text alone is never executable.
    """
    status: str
    strategy_id: str
    proposal: ProposalBase | None = None
    advisory_text: str | None = None
    advisory_artifact_ref: str | None = None
    reason: str | None = None
    reason_codes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        valid = {
            STATUS_EXECUTABLE, STATUS_DECLINED, STATUS_ADVISORY,
            STATUS_NEEDS_REVIEW, STATUS_FAILED, STATUS_POLICY_DENIED,
        }
        if self.status not in valid:
            raise ValueError(f"invalid_propose_strategy_result_status: {self.status!r}")
        if self.status == STATUS_EXECUTABLE and (self.proposal is None or not isinstance(self.proposal, ExecutableProposal)):
            raise ValueError("executable_result_requires_ExecutableProposal")
        if self.status != STATUS_EXECUTABLE and self.proposal is not None and isinstance(self.proposal, ExecutableProposal):
            raise ValueError("non_executable_result_must_not_carry_ExecutableProposal")

    @property
    def is_executable(self) -> bool:
        return self.status == STATUS_EXECUTABLE

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @classmethod
    def executable(
        cls,
        strategy_id: str,
        proposal: ExecutableProposal,
        reason: str | None = None,
        reason_codes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ProposeStrategyResult":
        return cls(
            status=STATUS_EXECUTABLE,
            strategy_id=strategy_id,
            proposal=proposal,
            reason=reason,
            reason_codes=list(reason_codes or []),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def declined(
        cls,
        strategy_id: str,
        reason: str | None = None,
        reason_codes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ProposeStrategyResult":
        return cls(
            status=STATUS_DECLINED,
            strategy_id=strategy_id,
            reason=reason,
            reason_codes=list(reason_codes or []),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def advisory(
        cls,
        strategy_id: str,
        advisory_text: str,
        advisory_artifact_ref: str | None = None,
        reason: str | None = None,
        reason_codes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ProposeStrategyResult":
        return cls(
            status=STATUS_ADVISORY,
            strategy_id=strategy_id,
            advisory_text=advisory_text,
            advisory_artifact_ref=advisory_artifact_ref,
            reason=reason,
            reason_codes=list(reason_codes or []),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def needs_review(
        cls,
        strategy_id: str,
        reason: str | None = None,
        reason_codes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ProposeStrategyResult":
        return cls(
            status=STATUS_NEEDS_REVIEW,
            strategy_id=strategy_id,
            reason=reason,
            reason_codes=list(reason_codes or []),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def failed(
        cls,
        strategy_id: str,
        reason: str | None = None,
        reason_codes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ProposeStrategyResult":
        return cls(
            status=STATUS_FAILED,
            strategy_id=strategy_id,
            reason=reason,
            reason_codes=list(reason_codes or []),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def policy_denied(
        cls,
        strategy_id: str,
        reason: str | None = None,
        reason_codes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ProposeStrategyResult":
        return cls(
            status=STATUS_POLICY_DENIED,
            strategy_id=strategy_id,
            reason=reason,
            reason_codes=list(reason_codes or []),
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "propose_strategy_result.v1",
            "status": self.status,
            "strategy_id": self.strategy_id,
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "advisory_text": self.advisory_text,
            "advisory_artifact_ref": self.advisory_artifact_ref,
            "reason": self.reason,
            "reason_codes": list(self.reason_codes),
            "metadata": dict(self.metadata),
        }


# ── ExecutableProposal validation helper (T001) ───────────────────────────────

def validate_executable_proposal(
    raw: "dict | ExecutableProposal",
) -> "tuple[str | None, list, str | None]":
    """Extract and validate command/tool_calls from a persisted proposal dict.

    Returns (command, tool_calls, reason).
    Raises ValueError when neither command nor tool_calls is present.
    """
    if isinstance(raw, ExecutableProposal):
        return raw.command, list(raw.tool_calls), raw.reason
    if not isinstance(raw, dict):
        raise ValueError(f"invalid_proposal_type: expected dict, got {type(raw).__name__}")
    command = (raw.get("command") or None)
    if command is not None:
        command = str(command).strip() or None
    tool_calls = raw.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        tool_calls = []
    reason = raw.get("reason") or None
    if not command and not tool_calls:
        raise ValueError("executable_proposal_requires_command_or_tool_calls")
    return command, list(tool_calls), reason
