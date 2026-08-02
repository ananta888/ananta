"""Revision-bound Separation-of-Duties policy used by validation and runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True, slots=True)
class DutyConflictRule:
    rule_id: str
    left_duty: str
    right_duty: str
    strict: bool = True


@dataclass(frozen=True, slots=True)
class DutyAssignment:
    principal_id: str
    role_slot_id: str
    team_id: str
    duties: frozenset[str]


@dataclass(frozen=True, slots=True)
class SeparationOfDutiesPolicy:
    policy_id: str
    revision: str
    mode: str = "strict"
    rules: tuple[DutyConflictRule, ...] = field(default_factory=tuple)

    @classmethod
    def enterprise_default(cls, *, revision: str = "1") -> "SeparationOfDutiesPolicy":
        return cls(
            policy_id="enterprise-organization-sod",
            revision=revision,
            mode="strict",
            rules=(
                DutyConflictRule("implementer-independent-reviewer", "implementer", "independent_reviewer"),
                DutyConflictRule("security-author-security-approver", "security_change_author", "security_approver"),
                DutyConflictRule("release-executor-final-approver", "release_executor", "go_no_go_approver"),
            ),
        )


@dataclass(frozen=True, slots=True)
class DutyConflict:
    policy_id: str
    rule_id: str
    principal_id: str
    role_slot_ids: tuple[str, ...]
    team_ids: tuple[str, ...]
    duties: tuple[str, str]


@dataclass(frozen=True, slots=True)
class SeparationOfDutiesDecision:
    allowed: bool
    reason_code: str
    policy_id: str
    policy_revision: str
    policy_hash: str
    conflicts: tuple[DutyConflict, ...]
    required_next_steps: tuple[str, ...]
    exception_ref: str | None = None


class SeparationOfDutiesService:
    """A pure validator shared by assignment, routing, handoff, and gates."""

    def evaluate(
        self,
        *,
        policy: SeparationOfDutiesPolicy,
        assignments: Iterable[DutyAssignment],
        risk: str = "medium",
        test_exception_ref: str | None = None,
        human_gate_ref: str | None = None,
        team_count: int | None = None,
    ) -> SeparationOfDutiesDecision:
        policy_hash = self.policy_hash(policy)
        if policy.mode not in {"strict", "bounded_test_exception"}:
            return self._deny(policy, policy_hash, "sod_policy_mode_unknown")
        assignment_rows = tuple(assignments)
        conflicts = self._conflicts(policy, assignment_rows)
        if not conflicts:
            return SeparationOfDutiesDecision(
                allowed=True,
                reason_code="sod_policy_satisfied",
                policy_id=policy.policy_id,
                policy_revision=policy.revision,
                policy_hash=policy_hash,
                conflicts=(),
                required_next_steps=(),
            )

        exception_allowed = (
            policy.mode == "bounded_test_exception"
            and team_count in {2, 3}
            and str(risk or "").lower() in {"none", "low"}
            and bool(str(test_exception_ref or "").strip())
            and bool(str(human_gate_ref or "").strip())
        )
        if exception_allowed:
            return SeparationOfDutiesDecision(
                allowed=True,
                reason_code="sod_test_exception_requires_human_gate",
                policy_id=policy.policy_id,
                policy_revision=policy.revision,
                policy_hash=policy_hash,
                conflicts=conflicts,
                required_next_steps=("human_gate_must_approve",),
                exception_ref=str(test_exception_ref),
            )
        return SeparationOfDutiesDecision(
            allowed=False,
            reason_code="sod_principal_collision",
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            policy_hash=policy_hash,
            conflicts=conflicts,
            required_next_steps=("reassign_conflicting_role_slot", "request_bound_human_exception"),
        )

    def enforce_runtime_operation(
        self,
        *,
        operation: str,
        actor_principal_id: str,
        source_assignments: Iterable[DutyAssignment],
        policy: SeparationOfDutiesPolicy,
        prior_conflict_principal_ids: Iterable[str] = (),
        exception_ref: str | None = None,
    ) -> SeparationOfDutiesDecision:
        assignments = tuple(source_assignments)
        actor = str(actor_principal_id or "").strip()
        if not actor:
            return self._deny(policy, self.policy_hash(policy), "sod_actor_missing")
        prior = {str(value or "").strip() for value in prior_conflict_principal_ids}
        if actor in prior and not exception_ref:
            return self._deny(policy, self.policy_hash(policy), "sod_prior_collision_persists_after_retry")
        decision = self.evaluate(policy=policy, assignments=assignments)
        if not decision.allowed:
            return decision
        operation_duty = {
            "handoff_accept": "independent_reviewer",
            "security_gate_approve": "security_approver",
            "release_gate_approve": "go_no_go_approver",
        }.get(str(operation or ""))
        if not operation_duty:
            return self._deny(policy, decision.policy_hash, "sod_operation_unknown")
        actor_duties = frozenset().union(*(item.duties for item in assignments if item.principal_id == actor))
        synthetic = DutyAssignment(actor, "runtime-operation", "hub", actor_duties | {operation_duty})
        return self.evaluate(policy=policy, assignments=(*assignments, synthetic))

    @staticmethod
    def policy_hash(policy: SeparationOfDutiesPolicy) -> str:
        payload = {
            "policy_id": policy.policy_id,
            "revision": policy.revision,
            "mode": policy.mode,
            "rules": [
                {
                    "rule_id": rule.rule_id,
                    "left_duty": rule.left_duty,
                    "right_duty": rule.right_duty,
                    "strict": rule.strict,
                }
                for rule in policy.rules
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _conflicts(
        policy: SeparationOfDutiesPolicy,
        assignments: tuple[DutyAssignment, ...],
    ) -> tuple[DutyConflict, ...]:
        by_principal: dict[str, list[DutyAssignment]] = {}
        for assignment in assignments:
            if assignment.principal_id:
                by_principal.setdefault(assignment.principal_id, []).append(assignment)
        conflicts: list[DutyConflict] = []
        for principal_id, rows in sorted(by_principal.items()):
            duties = frozenset().union(*(row.duties for row in rows))
            for rule in policy.rules:
                if rule.strict and rule.left_duty in duties and rule.right_duty in duties:
                    relevant = tuple(
                        row for row in rows if rule.left_duty in row.duties or rule.right_duty in row.duties
                    )
                    conflicts.append(
                        DutyConflict(
                            policy_id=policy.policy_id,
                            rule_id=rule.rule_id,
                            principal_id=principal_id,
                            role_slot_ids=tuple(sorted({row.role_slot_id for row in relevant})),
                            team_ids=tuple(sorted({row.team_id for row in relevant})),
                            duties=(rule.left_duty, rule.right_duty),
                        )
                    )
        return tuple(conflicts)

    @staticmethod
    def _deny(
        policy: SeparationOfDutiesPolicy,
        policy_hash: str,
        reason_code: str,
    ) -> SeparationOfDutiesDecision:
        return SeparationOfDutiesDecision(
            allowed=False,
            reason_code=reason_code,
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            policy_hash=policy_hash,
            conflicts=(),
            required_next_steps=("request_policy_review",),
        )


__all__ = [
    "DutyAssignment",
    "DutyConflict",
    "DutyConflictRule",
    "SeparationOfDutiesDecision",
    "SeparationOfDutiesPolicy",
    "SeparationOfDutiesService",
]
