"""Hub-owned automated approval issuance for Organization workflow gates.

The service converts an already persisted Hub verification result into one
revision-bound approval.  It never trusts Worker-supplied reviewer identity:
the reviewer is selected from active Organization role assignments backed by
the registered Agent directory, and the complete request plus its opaque Task
reference are staged in the caller's transaction.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from sqlmodel import select

from agent.db_models import ApprovalRequestDB
from agent.services.approval_request_service import compute_arguments_digest
from agent.services.organization_workflow_completion_policy_service import (
    ORGANIZATION_WORKFLOW_APPROVAL_REF_FIELD,
    ORGANIZATION_WORKFLOW_APPROVAL_REF_SCHEMA,
    ORGANIZATION_WORKFLOW_APPROVAL_TOOL,
    ORGANIZATION_WORKFLOW_AUTHORITY_SCHEMA,
    ORGANIZATION_WORKFLOW_AUTOMATED_DECISION_MODE,
    ORGANIZATION_WORKFLOW_WAITING_REASON,
    OrganizationWorkflowCompletionPolicyService,
    organization_workflow_completion_policy_service,
)
from agent.services.organization_workflow_gate_reviewer_service import (
    OrganizationWorkflowGateReviewerService,
    organization_workflow_gate_reviewer_service,
)


@dataclass(frozen=True, slots=True)
class OrganizationWorkflowGateApprovalIssuance:
    applicable: bool
    issued: bool
    replayed: bool = False
    reason_code: str | None = None
    approval_request_id: str | None = None
    approval_assignment_id: str | None = None
    policy_hash: str | None = None


class OrganizationWorkflowGateApprovalService:
    """Select an eligible role Agent and stage an idempotent Hub grant."""

    def __init__(
        self,
        *,
        completion_policy: OrganizationWorkflowCompletionPolicyService | None = None,
        reviewer_service: OrganizationWorkflowGateReviewerService | None = None,
    ) -> None:
        self._completion_policy = completion_policy or organization_workflow_completion_policy_service
        self._reviewers = reviewer_service or organization_workflow_gate_reviewer_service

    def issue_for_verified_completion(
        self,
        *,
        authoritative_task: Any | None,
        candidate_task: Any,
        session: Any,
        now: float | None = None,
    ) -> OrganizationWorkflowGateApprovalIssuance:
        """Stage one grant and its Task reference in the current transaction."""

        binding = self._completion_policy.required_binding(authoritative_task or candidate_task)
        if binding is None or str(getattr(candidate_task, "status", "")) != "completed":
            return OrganizationWorkflowGateApprovalIssuance(
                applicable=binding is not None,
                issued=False,
            )
        if self._approval_reference(candidate_task) is not None:
            return OrganizationWorkflowGateApprovalIssuance(
                applicable=True,
                issued=False,
                reason_code="organization_workflow_gate_approval_already_referenced",
            )

        preflight = self._completion_policy.evaluate(
            authoritative_task=authoritative_task,
            candidate_task=candidate_task,
            session=session,
            now=now,
        )
        if preflight.allowed or preflight.reason_code != ORGANIZATION_WORKFLOW_WAITING_REASON:
            return OrganizationWorkflowGateApprovalIssuance(
                applicable=True,
                issued=False,
                reason_code=preflight.reason_code,
                policy_hash=preflight.binding_digest,
            )

        reviewer = self._reviewers.select(
            task=authoritative_task or candidate_task,
            binding=binding,
            session=session,
        )
        if reviewer is None:
            return OrganizationWorkflowGateApprovalIssuance(
                applicable=True,
                issued=False,
                reason_code="organization_workflow_gate_automated_reviewer_unavailable",
            )
        task = authoritative_task or candidate_task
        binding_digest = self._completion_policy.binding_fingerprint(
            task=task,
            binding=binding,
        )
        verification = self._latest_verification(session=session, task=task)
        verification_id = str(getattr(verification, "id", "") or "")
        intent_key = self._intent_key(
            task=task,
            binding_digest=binding_digest,
            verification_id=verification_id,
            approval_assignment_id=reviewer.assignment_id,
            policy_hash=reviewer.policy_hash,
        )
        existing = session.exec(
            select(ApprovalRequestDB).where(ApprovalRequestDB.approval_intent_key == intent_key)
        ).one_or_none()
        if existing is not None:
            if not self._replay_matches(
                request=existing,
                task=task,
                binding=binding,
                binding_digest=binding_digest,
                approval_assignment_id=reviewer.assignment_id,
                principal_id=reviewer.principal_id,
            ):
                return OrganizationWorkflowGateApprovalIssuance(
                    applicable=True,
                    issued=False,
                    reason_code="organization_workflow_gate_approval_replay_conflict",
                    policy_hash=reviewer.policy_hash,
                )
            self._attach_reference(candidate_task, existing.id)
            return OrganizationWorkflowGateApprovalIssuance(
                applicable=True,
                issued=True,
                replayed=True,
                approval_request_id=existing.id,
                approval_assignment_id=reviewer.assignment_id,
                policy_hash=reviewer.policy_hash,
            )

        timestamp = time.time() if now is None else float(now)
        arguments = self._completion_policy.approval_arguments(
            task=task,
            binding=binding,
            approval_assignment_id=reviewer.assignment_id,
        )
        request_id = f"organization-workflow-gate-{intent_key[:32]}"
        request = ApprovalRequestDB(
            id=request_id,
            task_id=str(getattr(task, "id", "") or ""),
            goal_id=str(getattr(task, "goal_id", "") or "") or None,
            tenant_id=str(getattr(task, "tenant_id", "") or "") or None,
            project_id=str(getattr(task, "project_id", "") or "") or None,
            organization_id=str(getattr(task, "organization_id", "") or "") or None,
            approval_intent_key=intent_key,
            tool_name=ORGANIZATION_WORKFLOW_APPROVAL_TOOL,
            canonical_arguments=arguments,
            arguments_digest=compute_arguments_digest(
                ORGANIZATION_WORKFLOW_APPROVAL_TOOL,
                arguments,
                binding_digest,
            ),
            target_fingerprint=binding_digest,
            risk_class="high",
            governance_mode="strict",
            status="granted",
            scope={
                "approval_class": "organization_workflow_gate",
                "decision_authority": {
                    "schema": ORGANIZATION_WORKFLOW_AUTHORITY_SCHEMA,
                    "mode": ORGANIZATION_WORKFLOW_AUTOMATED_DECISION_MODE,
                    "approval_assignment_id": reviewer.assignment_id,
                    "verification_record_id": verification_id,
                    "policy_id": reviewer.policy_id,
                    "policy_revision": reviewer.policy_revision,
                    "policy_hash": reviewer.policy_hash,
                },
            },
            created_at=timestamp,
            expires_at=timestamp + 3600,
            decided_at=timestamp,
            decided_by=reviewer.principal_id,
            decision_reason="hub_policy:verified_role_assignment",
        )
        session.add(request)
        session.flush()
        self._attach_reference(candidate_task, request.id)
        return OrganizationWorkflowGateApprovalIssuance(
            applicable=True,
            issued=True,
            approval_request_id=request.id,
            approval_assignment_id=reviewer.assignment_id,
            policy_hash=reviewer.policy_hash,
        )

    @staticmethod
    def _latest_verification(*, session, task):
        from agent.db_models import VerificationRecordDB

        return session.exec(
            select(VerificationRecordDB)
            .where(VerificationRecordDB.task_id == str(task.id))
            .order_by(
                VerificationRecordDB.updated_at.desc(),
                VerificationRecordDB.created_at.desc(),
                VerificationRecordDB.id.desc(),
            )
        ).first()

    @staticmethod
    def _intent_key(
        *,
        task,
        binding_digest: str,
        verification_id: str,
        approval_assignment_id: str,
        policy_hash: str,
    ) -> str:
        payload = {
            "schema": "organization_workflow_gate_approval_intent.v1",
            "tenant_id": str(task.tenant_id or ""),
            "project_id": str(task.project_id or ""),
            "organization_id": str(task.organization_id or ""),
            "task_id": str(task.id or ""),
            "binding_digest": binding_digest,
            "verification_record_id": verification_id,
            "approval_assignment_id": approval_assignment_id,
            "policy_hash": policy_hash,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _replay_matches(
        self,
        *,
        request,
        task,
        binding,
        binding_digest,
        approval_assignment_id,
        principal_id,
    ) -> bool:
        return (
            request.status == "granted"
            and request.tool_name == ORGANIZATION_WORKFLOW_APPROVAL_TOOL
            and request.task_id == str(task.id)
            and request.target_fingerprint == binding_digest
            and request.decided_by == principal_id
            and dict(request.canonical_arguments or {})
            == self._completion_policy.approval_arguments(
                task=task,
                binding=binding,
                approval_assignment_id=approval_assignment_id,
            )
        )

    @staticmethod
    def _approval_reference(task) -> dict[str, Any] | None:
        raw = dict(getattr(task, "verification_status", None) or {}).get(ORGANIZATION_WORKFLOW_APPROVAL_REF_FIELD)
        return dict(raw) if isinstance(raw, dict) else None

    @staticmethod
    def _attach_reference(task, request_id: str) -> None:
        verification = dict(getattr(task, "verification_status", None) or {})
        verification[ORGANIZATION_WORKFLOW_APPROVAL_REF_FIELD] = {
            "schema": ORGANIZATION_WORKFLOW_APPROVAL_REF_SCHEMA,
            "approval_request_id": request_id,
        }
        task.verification_status = verification


organization_workflow_gate_approval_service = OrganizationWorkflowGateApprovalService()


__all__ = [
    "OrganizationWorkflowGateApprovalIssuance",
    "OrganizationWorkflowGateApprovalService",
    "organization_workflow_gate_approval_service",
]
