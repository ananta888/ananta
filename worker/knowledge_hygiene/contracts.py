"""Worker input boundary; assignments are immutable and Hub-authored."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ananta_contracts.knowledge_hygiene import (
    KnowledgeHygieneRun,
    SourceRevisionBinding,
    require_grounded_source_id,
    require_sha256,
)


class KnowledgeHygieneWorkerError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class KnowledgeHygieneAssignment:
    run_id: str
    project_id: str
    assignment_digest: str
    source_bindings: tuple[SourceRevisionBinding, ...]
    allowed_operations: tuple[str, ...]
    profile_name: str
    policy_version: str
    budgets: Mapping[str, int]
    expires_at: float

    def __post_init__(self) -> None:
        require_grounded_source_id(self.run_id)
        require_sha256(self.assignment_digest, "invalid_assignment_digest")
        if not self.source_bindings:
            raise KnowledgeHygieneWorkerError("source_bindings_required")
        if not set(self.allowed_operations).issubset(
            {"extract_claims", "synthesize_wiki", "analyze_candidates", "propose_correction", "materialize_graph"}
        ):
            raise KnowledgeHygieneWorkerError("unknown_worker_operation")
        calculated = KnowledgeHygieneRun.calculate_assignment_digest(
            run_id=self.run_id,
            project_id=self.project_id,
            source_bindings=self.source_bindings,
            policy_version=self.policy_version,
            profile_name=self.profile_name,
            budgets=self.budgets,
        )
        if calculated != self.assignment_digest:
            raise KnowledgeHygieneWorkerError("assignment_digest_mismatch")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "KnowledgeHygieneAssignment":
        return cls(
            run_id=str(raw.get("run_id") or ""),
            project_id=str(raw.get("project_id") or ""),
            assignment_digest=str(raw.get("assignment_digest") or ""),
            source_bindings=tuple(
                SourceRevisionBinding.from_mapping(item)
                for item in raw.get("source_bindings") or ()
            ),
            allowed_operations=tuple(str(item) for item in raw.get("allowed_operations") or ()),
            profile_name=str(raw.get("profile_name") or ""),
            policy_version=str(raw.get("policy_version") or ""),
            budgets={str(key): int(value) for key, value in dict(raw.get("budgets") or {}).items()},
            expires_at=float(raw.get("expires_at") or 0.0),
        )

    def require_operation(self, operation: str, *, now: float) -> None:
        if now > self.expires_at:
            raise KnowledgeHygieneWorkerError("assignment_expired")
        if operation not in self.allowed_operations:
            raise KnowledgeHygieneWorkerError("operation_outside_assignment")

    def binding_for(self, source_id: str, source_revision: str, locator: str) -> SourceRevisionBinding:
        for binding in self.source_bindings:
            if binding.source_id == source_id and binding.source_revision == source_revision:
                if locator not in binding.allowed_locators:
                    raise KnowledgeHygieneWorkerError("locator_outside_assignment")
                return binding
        raise KnowledgeHygieneWorkerError("source_outside_assignment")
