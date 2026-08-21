"""Versioned, transport-neutral contracts for Knowledge Hygiene v1.

The contracts deliberately contain no persistence or orchestration behavior.  The
Hub owns those concerns; workers may only return data conforming to these types.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence


CONTRACT_VERSION = "knowledge_hygiene.v1"
GROUNDED_SOURCE_ID_PATTERN = re.compile(r"^(?:SRC|RUN)_[0-9]{4}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeHygieneContractError(ValueError):
    """Stable fail-closed contract error exposed across Hub/Worker boundaries."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class CoverageState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class ConflictState(StrEnum):
    OPEN = "open"
    PENDING_REINGEST = "pending_reingest"
    RESOLVED = "resolved"
    REOPENED = "reopened"


class RunState(StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    PARTIAL = "partial"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DecisionKind(StrEnum):
    KEEP_LEFT = "keep_left"
    KEEP_RIGHT = "keep_right"
    KEEP_BOTH = "keep_both"
    REQUEST_CORRECTION = "request_correction"
    DISMISS_NOT_CONFLICT = "dismiss_not_conflict"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    return value


def require_sha256(value: str, reason_code: str) -> str:
    normalized = str(value or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(normalized):
        raise KnowledgeHygieneContractError(reason_code)
    return normalized


def require_grounded_source_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not GROUNDED_SOURCE_ID_PATTERN.fullmatch(normalized):
        raise KnowledgeHygieneContractError("unverified_source_id")
    return normalized


def require_non_empty(value: str, reason_code: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise KnowledgeHygieneContractError(reason_code)
    return normalized


def _coverage(value: CoverageState | str) -> CoverageState:
    try:
        return CoverageState(value)
    except ValueError as exc:
        raise KnowledgeHygieneContractError("invalid_coverage") from exc


@dataclass(frozen=True, slots=True)
class SourceRevisionBinding:
    source_id: str
    source_revision: str
    content_sha256: str
    allowed_locators: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", require_grounded_source_id(self.source_id))
        object.__setattr__(
            self,
            "source_revision",
            require_non_empty(self.source_revision, "source_revision_required"),
        )
        object.__setattr__(
            self,
            "content_sha256",
            require_sha256(self.content_sha256, "invalid_source_content_sha256"),
        )
        locators = tuple(sorted({require_non_empty(item, "source_locator_required") for item in self.allowed_locators}))
        if not locators:
            raise KnowledgeHygieneContractError("source_locators_required")
        object.__setattr__(self, "allowed_locators", locators)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SourceRevisionBinding":
        return cls(
            source_id=str(raw.get("source_id") or ""),
            source_revision=str(raw.get("source_revision") or ""),
            content_sha256=str(raw.get("content_sha256") or ""),
            allowed_locators=tuple(str(item) for item in raw.get("allowed_locators") or ()),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeClaim:
    claim_id: str
    project_id: str
    revision: int
    subject: str
    predicate: str
    value: Any
    source_id: str
    source_revision: str
    source_locator: str
    source_content_sha256: str
    extraction_run_id: str
    scope: str = "project"
    unit: str | None = None
    status: str | None = None
    assertion_kind: str = "actual"
    effective_from: str | None = None
    effective_to: str | None = None
    confidence: float = 1.0
    coverage: CoverageState = CoverageState.COMPLETE
    created_at: float = 0.0
    supersedes_claim_refs: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("claim_id", "project_id", "subject", "predicate", "source_locator"):
            object.__setattr__(
                self,
                field_name,
                require_non_empty(getattr(self, field_name), f"{field_name}_required"),
            )
        object.__setattr__(self, "source_id", require_grounded_source_id(self.source_id))
        object.__setattr__(self, "extraction_run_id", require_grounded_source_id(self.extraction_run_id))
        object.__setattr__(
            self,
            "source_revision",
            require_non_empty(self.source_revision, "source_revision_required"),
        )
        object.__setattr__(
            self,
            "source_content_sha256",
            require_sha256(self.source_content_sha256, "invalid_source_content_sha256"),
        )
        if self.revision < 1:
            raise KnowledgeHygieneContractError("invalid_claim_revision")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise KnowledgeHygieneContractError("invalid_claim_confidence")
        if self.assertion_kind not in {"actual", "target", "estimate", "hypothesis"}:
            raise KnowledgeHygieneContractError("invalid_assertion_kind")
        if any(claim_id == self.claim_id and revision == self.revision for claim_id, revision in self.supersedes_claim_refs):
            raise KnowledgeHygieneContractError("claim_cannot_supersede_itself")
        try:
            canonical_json(self.value)
        except (TypeError, ValueError) as exc:
            raise KnowledgeHygieneContractError("claim_value_not_json") from exc
        object.__setattr__(self, "coverage", _coverage(self.coverage))

    @property
    def normalized_payload(self) -> dict[str, Any]:
        return {
            "subject": " ".join(self.subject.casefold().split()),
            "predicate": " ".join(self.predicate.casefold().split()),
            "value": self.value,
            "unit": self.unit.casefold().strip() if self.unit else None,
            "status": self.status.casefold().strip() if self.status else None,
            "assertion_kind": self.assertion_kind,
            "scope": self.scope,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "supersedes_claim_refs": self.supersedes_claim_refs,
        }

    @property
    def record_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def idempotency_key(self) -> str:
        return canonical_digest(
            {
                "project_id": self.project_id,
                "source_id": self.source_id,
                "source_revision": self.source_revision,
                "source_locator": self.source_locator,
                "payload": self.normalized_payload,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        payload = _json_ready(asdict(self))
        payload["coverage"] = self.coverage.value
        return payload

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "KnowledgeClaim":
        return cls(
            claim_id=str(raw.get("claim_id") or ""),
            project_id=str(raw.get("project_id") or ""),
            revision=int(raw.get("revision") or 1),
            subject=str(raw.get("subject") or ""),
            predicate=str(raw.get("predicate") or ""),
            value=raw.get("value"),
            source_id=str(raw.get("source_id") or ""),
            source_revision=str(raw.get("source_revision") or ""),
            source_locator=str(raw.get("source_locator") or ""),
            source_content_sha256=str(raw.get("source_content_sha256") or ""),
            extraction_run_id=str(raw.get("extraction_run_id") or ""),
            scope=str(raw.get("scope") or "project"),
            unit=str(raw["unit"]) if raw.get("unit") is not None else None,
            status=str(raw["status"]) if raw.get("status") is not None else None,
            assertion_kind=str(raw.get("assertion_kind") or "actual"),
            effective_from=str(raw["effective_from"]) if raw.get("effective_from") else None,
            effective_to=str(raw["effective_to"]) if raw.get("effective_to") else None,
            confidence=float(raw.get("confidence", 1.0)),
            coverage=_coverage(str(raw.get("coverage") or "complete")),
            created_at=float(raw.get("created_at") or 0.0),
            supersedes_claim_refs=tuple(
                (str(item[0]), int(item[1]))
                for item in raw.get("supersedes_claim_refs") or ()
            ),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeConflict:
    conflict_id: str
    project_id: str
    left_claim_id: str
    left_claim_revision: int
    left_claim_digest: str
    right_claim_id: str
    right_claim_revision: int
    right_claim_digest: str
    conflict_type: str
    severity: str
    evidence: tuple[str, ...]
    coverage: CoverageState
    state: ConflictState = ConflictState.OPEN
    version: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    resolved_by_run_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "conflict_id",
            "project_id",
            "left_claim_id",
            "right_claim_id",
            "conflict_type",
            "severity",
        ):
            object.__setattr__(
                self,
                field_name,
                require_non_empty(getattr(self, field_name), f"{field_name}_required"),
            )
        object.__setattr__(self, "left_claim_digest", require_sha256(self.left_claim_digest, "invalid_left_claim_digest"))
        object.__setattr__(self, "right_claim_digest", require_sha256(self.right_claim_digest, "invalid_right_claim_digest"))
        object.__setattr__(self, "coverage", _coverage(self.coverage))
        try:
            object.__setattr__(self, "state", ConflictState(self.state))
        except ValueError as exc:
            raise KnowledgeHygieneContractError("invalid_conflict_state") from exc
        if self.left_claim_id == self.right_claim_id:
            raise KnowledgeHygieneContractError("self_conflict_forbidden")
        if self.version < 1:
            raise KnowledgeHygieneContractError("invalid_conflict_version")

    @property
    def basis_digest(self) -> str:
        return canonical_digest(
            {
                "conflict_id": self.conflict_id,
                "version": self.version,
                "left": [self.left_claim_id, self.left_claim_revision, self.left_claim_digest],
                "right": [self.right_claim_id, self.right_claim_revision, self.right_claim_digest],
                "type": self.conflict_type,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        payload = _json_ready(asdict(self))
        payload["coverage"] = self.coverage.value
        payload["state"] = self.state.value
        payload["basis_digest"] = self.basis_digest
        return payload

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "KnowledgeConflict":
        return cls(
            conflict_id=str(raw.get("conflict_id") or ""),
            project_id=str(raw.get("project_id") or ""),
            left_claim_id=str(raw.get("left_claim_id") or ""),
            left_claim_revision=int(raw.get("left_claim_revision") or 1),
            left_claim_digest=str(raw.get("left_claim_digest") or ""),
            right_claim_id=str(raw.get("right_claim_id") or ""),
            right_claim_revision=int(raw.get("right_claim_revision") or 1),
            right_claim_digest=str(raw.get("right_claim_digest") or ""),
            conflict_type=str(raw.get("conflict_type") or ""),
            severity=str(raw.get("severity") or "unknown"),
            evidence=tuple(str(item) for item in raw.get("evidence") or ()),
            coverage=_coverage(str(raw.get("coverage") or "unknown")),
            state=ConflictState(str(raw.get("state") or "open")),
            version=int(raw.get("version") or 1),
            created_at=float(raw.get("created_at") or 0.0),
            updated_at=float(raw.get("updated_at") or 0.0),
            resolved_by_run_id=str(raw["resolved_by_run_id"]) if raw.get("resolved_by_run_id") else None,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeConflictDecision:
    decision_id: str
    conflict_id: str
    project_id: str
    expected_conflict_version: int
    actor_id: str
    actor_kind: str
    decision: DecisionKind
    rationale: str
    qualifiers: tuple[str, ...]
    basis_digest: str
    created_at: float
    writeback_requested: bool = False
    second_approver_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("decision_id", "conflict_id", "project_id", "actor_id", "rationale"):
            object.__setattr__(
                self,
                field_name,
                require_non_empty(getattr(self, field_name), f"{field_name}_required"),
            )
        if self.actor_kind != "human":
            raise KnowledgeHygieneContractError("human_actor_required")
        try:
            object.__setattr__(self, "decision", DecisionKind(self.decision))
        except ValueError as exc:
            raise KnowledgeHygieneContractError("invalid_decision") from exc
        object.__setattr__(self, "basis_digest", require_sha256(self.basis_digest, "invalid_basis_digest"))
        if self.expected_conflict_version < 1:
            raise KnowledgeHygieneContractError("invalid_expected_conflict_version")

    def to_dict(self) -> dict[str, Any]:
        payload = _json_ready(asdict(self))
        payload["decision"] = self.decision.value
        return payload

    def idempotency_payload(self) -> dict[str, Any]:
        """Return the stable request identity used to validate decision replays."""
        payload = self.to_dict()
        payload.pop("created_at", None)
        return payload


@dataclass(frozen=True, slots=True)
class CuratedWikiPage:
    page_id: str
    project_id: str
    slug: str
    title: str
    revision: int
    body_markdown: str
    claim_refs: tuple[tuple[str, int], ...]
    conflict_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    aliases: tuple[str, ...]
    coverage: CoverageState
    created_at: float

    def __post_init__(self) -> None:
        for field_name in ("page_id", "project_id", "slug", "title"):
            object.__setattr__(
                self,
                field_name,
                require_non_empty(getattr(self, field_name), f"{field_name}_required"),
            )
        if self.revision < 1:
            raise KnowledgeHygieneContractError("invalid_wiki_revision")
        for source_id in self.source_refs:
            require_grounded_source_id(source_id)
        object.__setattr__(self, "coverage", _coverage(self.coverage))

    @property
    def content_hash(self) -> str:
        return canonical_digest(
            {
                "title": self.title,
                "body_markdown": self.body_markdown,
                "claim_refs": self.claim_refs,
                "conflict_refs": self.conflict_refs,
                "source_refs": self.source_refs,
                "coverage": self.coverage.value,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        payload = _json_ready(asdict(self))
        payload["coverage"] = self.coverage.value
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CuratedWikiPage":
        return cls(
            page_id=str(raw.get("page_id") or ""),
            project_id=str(raw.get("project_id") or ""),
            slug=str(raw.get("slug") or ""),
            title=str(raw.get("title") or ""),
            revision=int(raw.get("revision") or 1),
            body_markdown=str(raw.get("body_markdown") or ""),
            claim_refs=tuple((str(item[0]), int(item[1])) for item in raw.get("claim_refs") or ()),
            conflict_refs=tuple(str(item) for item in raw.get("conflict_refs") or ()),
            source_refs=tuple(str(item) for item in raw.get("source_refs") or ()),
            aliases=tuple(str(item) for item in raw.get("aliases") or ()),
            coverage=_coverage(str(raw.get("coverage") or "unknown")),
            created_at=float(raw.get("created_at") or 0.0),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeHygieneRun:
    run_id: str
    project_id: str
    state: RunState
    source_bindings: tuple[SourceRevisionBinding, ...]
    policy_version: str
    profile_name: str
    budgets: Mapping[str, int]
    actor_id: str
    coverage: CoverageState
    assignment_digest: str
    result_digest: str | None = None
    checkpoint: int = 0
    lease_owner: str | None = None
    lease_expires_at: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", require_grounded_source_id(self.run_id))
        for field_name in ("project_id", "policy_version", "profile_name", "actor_id"):
            object.__setattr__(
                self,
                field_name,
                require_non_empty(getattr(self, field_name), f"{field_name}_required"),
            )
        try:
            object.__setattr__(self, "state", RunState(self.state))
        except ValueError as exc:
            raise KnowledgeHygieneContractError("invalid_run_state") from exc
        object.__setattr__(self, "coverage", _coverage(self.coverage))
        object.__setattr__(self, "assignment_digest", require_sha256(self.assignment_digest, "invalid_assignment_digest"))
        if self.result_digest is not None:
            object.__setattr__(self, "result_digest", require_sha256(self.result_digest, "invalid_result_digest"))
        if not self.source_bindings:
            raise KnowledgeHygieneContractError("source_bindings_required")
        if any(int(value) < 0 for value in self.budgets.values()):
            raise KnowledgeHygieneContractError("invalid_run_budget")

    @staticmethod
    def calculate_assignment_digest(
        *,
        run_id: str,
        project_id: str,
        source_bindings: Sequence[SourceRevisionBinding],
        policy_version: str,
        profile_name: str,
        budgets: Mapping[str, int],
    ) -> str:
        return canonical_digest(
            {
                "version": CONTRACT_VERSION,
                "run_id": run_id,
                "project_id": project_id,
                "source_bindings": [asdict(item) for item in source_bindings],
                "policy_version": policy_version,
                "profile_name": profile_name,
                "budgets": dict(sorted(budgets.items())),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        payload = _json_ready(asdict(self))
        payload["state"] = self.state.value
        payload["coverage"] = self.coverage.value
        return payload

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "KnowledgeHygieneRun":
        return cls(
            run_id=str(raw.get("run_id") or ""),
            project_id=str(raw.get("project_id") or ""),
            state=RunState(str(raw.get("state") or "pending")),
            source_bindings=tuple(SourceRevisionBinding.from_mapping(item) for item in raw.get("source_bindings") or ()),
            policy_version=str(raw.get("policy_version") or ""),
            profile_name=str(raw.get("profile_name") or ""),
            budgets={str(key): int(value) for key, value in dict(raw.get("budgets") or {}).items()},
            actor_id=str(raw.get("actor_id") or ""),
            coverage=_coverage(str(raw.get("coverage") or "unknown")),
            assignment_digest=str(raw.get("assignment_digest") or ""),
            result_digest=str(raw["result_digest"]) if raw.get("result_digest") else None,
            checkpoint=int(raw.get("checkpoint") or 0),
            lease_owner=str(raw["lease_owner"]) if raw.get("lease_owner") else None,
            lease_expires_at=float(raw["lease_expires_at"]) if raw.get("lease_expires_at") is not None else None,
            created_at=float(raw.get("created_at") or 0.0),
            updated_at=float(raw.get("updated_at") or 0.0),
            failure_reason=str(raw["failure_reason"]) if raw.get("failure_reason") else None,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeHealthSnapshot:
    snapshot_id: str
    project_id: str
    as_of: float
    scope_version: str
    coverage: CoverageState
    counts: Mapping[str, int | None]
    oldest_open_age_seconds: float | None
    trend: Mapping[str, float | None]
    basis_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "coverage", _coverage(self.coverage))
        object.__setattr__(self, "basis_digest", require_sha256(self.basis_digest, "invalid_health_basis_digest"))
        if self.coverage is not CoverageState.COMPLETE:
            forbidden_false_zero = any(
                self.counts.get(key) == 0
                for key in ("claims", "conflicts", "open_conflicts", "wiki_pages")
            )
            if forbidden_false_zero:
                raise KnowledgeHygieneContractError("false_zero_for_incomplete_coverage")

    def to_dict(self) -> dict[str, Any]:
        payload = _json_ready(asdict(self))
        payload["coverage"] = self.coverage.value
        return payload


@dataclass(frozen=True, slots=True)
class CorrectionProposal:
    correction_id: str
    project_id: str
    conflict_id: str
    source_id: str
    source_revision: str
    source_locator: str
    base_content_sha256: str
    proposed_content: str
    proposal_digest: str
    proposed_by_run_id: str
    created_at: float
    writeback_approved_by: str | None = None
    writeback_approved_at: float | None = None

    def __post_init__(self) -> None:
        for field_name in ("correction_id", "project_id", "conflict_id", "source_revision", "source_locator"):
            object.__setattr__(
                self,
                field_name,
                require_non_empty(getattr(self, field_name), f"{field_name}_required"),
            )
        object.__setattr__(self, "source_id", require_grounded_source_id(self.source_id))
        object.__setattr__(self, "proposed_by_run_id", require_grounded_source_id(self.proposed_by_run_id))
        object.__setattr__(self, "base_content_sha256", require_sha256(self.base_content_sha256, "invalid_base_content_sha256"))
        object.__setattr__(self, "proposal_digest", require_sha256(self.proposal_digest, "invalid_proposal_digest"))
        if self.proposal_digest != canonical_digest(
            {
                "project_id": self.project_id,
                "conflict_id": self.conflict_id,
                "source_id": self.source_id,
                "source_revision": self.source_revision,
                "source_locator": self.source_locator,
                "base_content_sha256": self.base_content_sha256,
                "proposed_content": self.proposed_content,
            }
        ):
            raise KnowledgeHygieneContractError("correction_digest_mismatch")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


def build_correction_proposal(
    *,
    correction_id: str,
    project_id: str,
    conflict_id: str,
    source_id: str,
    source_revision: str,
    source_locator: str,
    base_content_sha256: str,
    proposed_content: str,
    proposed_by_run_id: str,
    created_at: float,
) -> CorrectionProposal:
    digest_payload = {
        "project_id": project_id,
        "conflict_id": conflict_id,
        "source_id": source_id,
        "source_revision": source_revision,
        "source_locator": source_locator,
        "base_content_sha256": base_content_sha256,
        "proposed_content": proposed_content,
    }
    return CorrectionProposal(
        correction_id=correction_id,
        project_id=project_id,
        conflict_id=conflict_id,
        source_id=source_id,
        source_revision=source_revision,
        source_locator=source_locator,
        base_content_sha256=base_content_sha256,
        proposed_content=proposed_content,
        proposal_digest=canonical_digest(digest_payload),
        proposed_by_run_id=proposed_by_run_id,
        created_at=created_at,
    )


@dataclass(frozen=True, slots=True)
class WorkerKnowledgeHygieneResult:
    run_id: str
    assignment_digest: str
    claims: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    wiki_pages: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    conflict_candidates: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    correction_proposals: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    coverage: CoverageState = CoverageState.UNKNOWN
    processed_records: int = 0
    rejected_records: int = 0

    @property
    def result_digest(self) -> str:
        return canonical_digest(
            {
                "run_id": self.run_id,
                "assignment_digest": self.assignment_digest,
                "claims": self.claims,
                "wiki_pages": self.wiki_pages,
                "conflict_candidates": self.conflict_candidates,
                "correction_proposals": self.correction_proposals,
                "coverage": CoverageState(self.coverage).value,
                "processed_records": self.processed_records,
                "rejected_records": self.rejected_records,
            }
        )
