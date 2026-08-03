from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

_POLICY_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas" / "policies" / "worker_task_proposal_policy.v1.json"
)
_EVIDENCE_REF = re.compile(r"^(?:SRC|RUN)_[0-9]{4}$")


@dataclass(frozen=True, slots=True)
class AssignmentProposalScope:
    tenant_id: str
    project_id: str
    organization_id: str
    goal_id: str
    source_task_id: str
    unit_id: str
    team_id: str
    role_slot_id: str
    assignment_id: str
    dispatch_lease_id: str
    worker_id: str
    role_template_ref: str
    source_task_status: str
    lease_active: bool
    allowed_task_kinds: frozenset[str]
    allowed_capabilities: frozenset[str]
    allowed_context_refs: frozenset[str]
    allowed_evidence_refs: frozenset[str]
    source_category_item_ids: frozenset[str]
    known_role_refs: frozenset[str]
    known_team_refs: frozenset[str]
    known_agent_refs: frozenset[str]
    remaining_budget: Mapping[str, float]
    amendment_depth: int = 0


def effective_proposal_policy_hash(policy: Mapping[str, Any]) -> str:
    rendered = json.dumps(dict(policy), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"sha256:{hashlib.sha256(rendered.encode('utf-8')).hexdigest()}"


class WorkerTaskProposalPolicyService:
    """Restrict-only role policy evaluated by the Hub."""

    def validate_policy(self, policy: Mapping[str, Any] | None) -> dict[str, Any]:
        candidate = dict(policy or {})
        if not candidate:
            candidate = self.default_deny_policy()
        issues: list[str] = []
        if _POLICY_SCHEMA_PATH.exists():
            schema = json.loads(_POLICY_SCHEMA_PATH.read_text(encoding="utf-8"))
            issues.extend(
                f"{'/'.join(map(str, error.path)) or '$'}:{error.message}"
                for error in sorted(Draft202012Validator(schema).iter_errors(candidate), key=lambda row: list(row.path))
            )
        else:
            issues.append("$:worker_task_proposal_policy_schema_missing")
        return {
            "valid": not issues,
            "issues": issues,
            "policy": candidate,
            "policy_hash": effective_proposal_policy_hash(candidate),
        }

    def evaluate(
        self,
        *,
        envelope: Mapping[str, Any],
        policy: Mapping[str, Any] | None,
        assignment: AssignmentProposalScope,
        proposal_count: int,
    ) -> dict[str, Any]:
        policy_result = self.validate_policy(policy)
        candidate = dict(policy_result["policy"])
        issues = list(policy_result["issues"])
        effective_hash = str(policy_result["policy_hash"])
        payload = dict(envelope.get("payload") or {})

        if not policy_result["valid"]:
            issues.append("proposal_policy_contract_invalid")
        if not candidate.get("may_propose_tasks", False):
            issues.append("proposal_policy_default_deny")
        if str(envelope.get("proposal_policy_hash") or "") != effective_hash:
            issues.append("proposal_policy_hash_stale")
        if str(envelope.get("proposing_role_template_ref") or "") != assignment.role_template_ref:
            issues.append("proposal_role_template_mismatch")
        self._check_exact_binding(envelope=envelope, assignment=assignment, issues=issues)
        if not assignment.lease_active:
            issues.append("proposal_dispatch_lease_inactive")
        if assignment.source_task_status not in {
            "assigned",
            "delegated",
            "in_progress",
            "verifying",
            "completed",
        }:
            issues.append("proposal_source_task_status_forbidden")
        if int(proposal_count) >= int(candidate.get("max_proposals_per_source_task") or 0):
            issues.append("proposal_count_limit_exceeded")
        if assignment.amendment_depth >= int(candidate.get("max_amendment_depth") or 0):
            issues.append("proposal_amendment_depth_exceeded")

        task_kind = str(payload.get("task_kind") or "")
        if task_kind not in set(candidate.get("allowed_task_kinds") or []):
            issues.append("proposal_task_kind_forbidden")
        if task_kind not in assignment.allowed_task_kinds:
            issues.append("proposal_task_kind_outside_assignment")
        if not set(payload.get("required_capabilities") or []).issubset(assignment.allowed_capabilities):
            issues.append("proposal_capability_escalation")
        if not set(payload.get("context_refs") or []).issubset(assignment.allowed_context_refs):
            issues.append("proposal_context_escalation")
        evidence_refs = {str(value) for value in list(payload.get("evidence_refs") or [])}
        if any(_EVIDENCE_REF.fullmatch(value) is None for value in evidence_refs):
            issues.append("proposal_evidence_ref_invalid")
        if not evidence_refs.issubset(assignment.allowed_evidence_refs):
            issues.append("proposal_evidence_escalation")
        source_items = {str(value) for value in list(envelope.get("source_category_item_ids") or [])}
        if not source_items:
            issues.append("proposal_category_scope_required")
        if not source_items.issubset(assignment.source_category_item_ids):
            issues.append("proposal_category_scope_expansion")

        suggestion_rules = dict(candidate.get("suggestion_constraints") or {})
        self._check_suggestions(
            payload=payload,
            assignment=assignment,
            rules=suggestion_rules,
            issues=issues,
        )
        self._check_budget(
            estimate=dict(payload.get("budget_estimate") or {}),
            limits=dict(candidate.get("budget_limits") or {}),
            remaining=dict(assignment.remaining_budget or {}),
            issues=issues,
        )
        approval_mode = str(dict(candidate.get("approval_policy") or {}).get("mode") or "always_reject")
        if approval_mode == "always_reject":
            issues.append("proposal_policy_always_reject")
        return {
            "allowed": not issues,
            "issues": list(dict.fromkeys(issues)),
            "reason_code": issues[0] if issues else "proposal_policy_allowed",
            "effective_policy_hash": effective_hash,
            "approval_mode": approval_mode,
            "target_scope": list(candidate.get("target_scope") or []),
        }

    @staticmethod
    def _check_exact_binding(
        *, envelope: Mapping[str, Any], assignment: AssignmentProposalScope, issues: list[str]
    ) -> None:
        fields = {
            "source_goal_id": assignment.goal_id,
            "source_task_id": assignment.source_task_id,
            "organization_id": assignment.organization_id,
            "unit_id": assignment.unit_id,
            "team_id": assignment.team_id,
            "role_slot_id": assignment.role_slot_id,
            "assignment_id": assignment.assignment_id,
            "dispatch_lease_id": assignment.dispatch_lease_id,
        }
        for field, expected in fields.items():
            if str(envelope.get(field) or "") != expected:
                issues.append(f"proposal_{field}_mismatch")

    @staticmethod
    def _check_suggestions(
        *,
        payload: Mapping[str, Any],
        assignment: AssignmentProposalScope,
        rules: Mapping[str, Any],
        issues: list[str],
    ) -> None:
        checks = (
            ("suggested_role_refs", "role_hints_allowed", assignment.known_role_refs),
            ("suggested_team_refs", "team_hints_allowed", assignment.known_team_refs),
            ("suggested_agent_refs", "agent_hints_allowed", assignment.known_agent_refs),
        )
        for field, flag, known in checks:
            refs = {str(value) for value in list(payload.get(field) or [])}
            if refs and not bool(rules.get(flag)):
                issues.append(f"proposal_{field}_forbidden")
            if refs and not refs.issubset(known):
                issues.append(f"proposal_{field}_unknown")
            if field == "suggested_agent_refs" and any("://" in value for value in refs):
                issues.append("proposal_direct_worker_address_forbidden")

    @staticmethod
    def _check_budget(
        *,
        estimate: Mapping[str, Any],
        limits: Mapping[str, Any],
        remaining: Mapping[str, float],
        issues: list[str],
    ) -> None:
        mappings = (
            ("estimated_tokens", "max_estimated_tokens"),
            ("estimated_seconds", "max_estimated_seconds"),
            ("estimated_cost_units", "max_estimated_cost_units"),
        )
        for estimate_key, limit_key in mappings:
            value = float(estimate.get(estimate_key) or 0)
            if value > float(limits.get(limit_key) or 0):
                issues.append(f"proposal_budget_policy_exceeded:{estimate_key}")
            remaining_value = remaining.get(estimate_key)
            if remaining_value is not None and value > float(remaining_value):
                issues.append(f"proposal_budget_assignment_exceeded:{estimate_key}")

    @staticmethod
    def default_deny_policy() -> dict[str, Any]:
        return {
            "schema": "worker_task_proposal_policy.v1",
            "key": "default_deny",
            "version": 1,
            "default_decision": "deny",
            "may_propose_tasks": False,
            "allowed_task_kinds": [],
            "target_scope": [],
            "max_proposals_per_source_task": 0,
            "max_amendment_depth": 0,
            "budget_limits": {
                "max_estimated_tokens": 0,
                "max_estimated_seconds": 0,
                "max_estimated_cost_units": 0,
            },
            "approval_policy": {
                "mode": "always_reject",
                "self_approval_allowed": False,
                "materialization_owner": "hub",
            },
            "scope_constraints": {
                "inheritance_mode": "restrict_only",
                "capabilities": "source_assignment_intersection",
                "tools": "source_assignment_intersection",
                "context": "source_assignment_intersection",
                "evidence": "source_assignment_allowlist",
                "tenant": "source_assignment_exact",
                "project": "source_assignment_exact",
                "organization": "source_assignment_exact",
                "budget": "source_assignment_remaining",
                "source_category_items": "existing_items_only",
            },
            "suggestion_constraints": {
                "role_hints_allowed": False,
                "team_hints_allowed": False,
                "agent_hints_allowed": False,
                "direct_worker_addresses_allowed": False,
                "queue_priority_override_allowed": False,
            },
            "revalidate_effective_policy_hash": True,
        }


__all__ = [
    "AssignmentProposalScope",
    "WorkerTaskProposalPolicyService",
    "effective_proposal_policy_hash",
]
