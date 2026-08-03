"""Hub-owned completion policy for revision-bound Organization workflow Tasks.

Workers may report execution and verification results, but a request payload is
never authority for a required Organization gate.  The authoritative evidence
is a persisted, granted :class:`ApprovalRequestDB` whose exact workflow binding
is reviewed by an active principal assignment for the configured approval role.

The policy is deliberately read-only.  Approval request creation and the UI/API
used to select an approving assignment remain separate responsibilities.  The
existing generic ApprovalRequest lifecycle does not issue this domain request
or attach its opaque reference to a Task; until a dedicated Hub issuance path
does so, required Organization workflow gates intentionally remain fail-closed.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlmodel import select

from agent.db_models import (
    ApprovalRequestDB,
    OrganizationInstanceDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    VerificationRecordDB,
)

ORGANIZATION_WORKFLOW_APPROVAL_REF_FIELD = "organization_workflow_gate_approval"
ORGANIZATION_WORKFLOW_APPROVAL_REF_SCHEMA = "organization_workflow_gate_approval_ref.v1"
ORGANIZATION_WORKFLOW_APPROVAL_TOOL = "organization.workflow_gate.complete"
ORGANIZATION_WORKFLOW_WAITING_REASON = "organization_workflow_gate_approval_required"


@dataclass(frozen=True)
class OrganizationWorkflowCompletionDecision:
    """One fail-closed decision returned to the Task repository boundary."""

    applicable: bool
    allowed: bool
    reason_code: str | None = None
    binding_digest: str | None = None
    approval_request_id: str | None = None


class OrganizationWorkflowCompletionPolicyService:
    """Validate required workflow-gate authority against Hub persistence."""

    BINDING_SCHEMA = "organization_workflow_step_binding.v1"

    def evaluate(
        self,
        *,
        authoritative_task: Any | None,
        candidate_task: Any,
        session: Any,
        now: float | None = None,
    ) -> OrganizationWorkflowCompletionDecision:
        task = authoritative_task or candidate_task
        binding = self._required_binding(task)
        candidate_binding = self._workflow_binding(candidate_task)
        if binding is None:
            binding = self._required_binding(candidate_task)
        if binding is None:
            return OrganizationWorkflowCompletionDecision(
                applicable=False,
                allowed=True,
            )

        binding_digest = self.binding_fingerprint(
            task=task,
            binding=binding,
        )
        if candidate_binding != binding:
            return self._denied(
                "organization_workflow_step_binding_immutable",
                binding_digest,
            )

        current_status = self._status(authoritative_task) if authoritative_task is not None else ""
        candidate_status = self._status(candidate_task)
        if candidate_status != "completed" or current_status == "completed":
            return OrganizationWorkflowCompletionDecision(
                applicable=True,
                allowed=True,
                binding_digest=binding_digest,
            )

        binding_error = self._binding_error(
            task=task,
            candidate=candidate_task,
            binding=binding,
        )
        if binding_error:
            return self._denied(binding_error, binding_digest)

        organization = session.exec(
            select(OrganizationInstanceDB).where(
                OrganizationInstanceDB.tenant_id == str(getattr(task, "tenant_id", "") or ""),
                OrganizationInstanceDB.project_id == str(getattr(task, "project_id", "") or ""),
                OrganizationInstanceDB.organization_id == str(getattr(task, "organization_id", "") or ""),
            )
        ).one_or_none()
        if organization is None or organization.lifecycle != "active":
            return self._denied(
                "organization_workflow_gate_organization_inactive",
                binding_digest,
            )
        if str(organization.definition_revision or "") != str(binding.get("definition_revision") or ""):
            return self._denied(
                "organization_workflow_gate_definition_drift",
                binding_digest,
            )

        verification = session.exec(
            select(VerificationRecordDB)
            .where(VerificationRecordDB.task_id == str(getattr(task, "id", "") or ""))
            .order_by(
                VerificationRecordDB.updated_at.desc(),
                VerificationRecordDB.created_at.desc(),
                VerificationRecordDB.id.desc(),
            )
        ).first()
        verification_error = self._verification_record_error(
            record=verification,
            task=task,
        )
        if verification_error:
            return self._denied(
                verification_error,
                binding_digest,
            )

        approval_ref = self._approval_ref(candidate_task)
        if approval_ref is None:
            return self._denied(
                ORGANIZATION_WORKFLOW_WAITING_REASON,
                binding_digest,
            )
        approval_request_id = str(approval_ref.get("approval_request_id") or "")
        request = session.get(ApprovalRequestDB, approval_request_id)
        request_error = self._approval_request_error(
            request=request,
            task=task,
            binding=binding,
            binding_digest=binding_digest,
            now=time.time() if now is None else float(now),
        )
        if request_error:
            return self._denied(
                request_error,
                binding_digest,
                approval_request_id=approval_request_id,
            )

        arguments = dict(request.canonical_arguments or {})
        approval_assignment_id = str(arguments.get("approval_assignment_id") or "")
        approval_assignment = session.get(
            OrganizationRoleAssignmentDB,
            approval_assignment_id,
        )
        approval_error = self._approval_assignment_error(
            session=session,
            assignment=approval_assignment,
            request=request,
            task=task,
            binding=binding,
        )
        if approval_error:
            return self._denied(
                approval_error,
                binding_digest,
                approval_request_id=approval_request_id,
            )

        gate = dict(binding["gate"])
        if gate["independent_principal_required"]:
            execution_error = self._independence_error(
                session=session,
                task=task,
                approval_principal_id=str(request.decided_by or ""),
            )
            if execution_error:
                return self._denied(
                    execution_error,
                    binding_digest,
                    approval_request_id=approval_request_id,
                )

        return OrganizationWorkflowCompletionDecision(
            applicable=True,
            allowed=True,
            binding_digest=binding_digest,
            approval_request_id=approval_request_id,
        )

    @classmethod
    def approval_arguments(
        cls,
        *,
        task: Any,
        binding: Mapping[str, Any],
        approval_assignment_id: str,
    ) -> dict[str, str]:
        """Build the exact content-free arguments for a Hub approval request."""

        gate = dict(binding.get("gate") or {})
        return {
            "schema": "organization_workflow_gate_completion_request.v1",
            "task_id": str(getattr(task, "id", "") or ""),
            "organization_id": str(binding.get("organization_id") or ""),
            "definition_revision": str(binding.get("definition_revision") or ""),
            "workflow_ref": str(binding.get("workflow_ref") or ""),
            "workflow_content_hash": str(binding.get("workflow_content_hash") or ""),
            "step_id": str(binding.get("step_id") or ""),
            "approval_role_ref": str(gate.get("approval_role_ref") or ""),
            "approval_assignment_id": str(approval_assignment_id or ""),
        }

    @classmethod
    def binding_fingerprint(
        cls,
        *,
        task: Any,
        binding: Mapping[str, Any],
    ) -> str:
        payload = {
            "schema": "organization_workflow_gate_completion_target.v1",
            "task_id": str(getattr(task, "id", "") or ""),
            "tenant_id": str(getattr(task, "tenant_id", "") or ""),
            "project_id": str(getattr(task, "project_id", "") or ""),
            "organization_id": str(binding.get("organization_id") or ""),
            "definition_revision": str(binding.get("definition_revision") or ""),
            "workflow_ref": str(binding.get("workflow_ref") or ""),
            "workflow_content_hash": str(binding.get("workflow_content_hash") or ""),
            "step_id": str(binding.get("step_id") or ""),
            "team_unit_id": str(binding.get("team_unit_id") or ""),
            "team_id": str(binding.get("team_id") or ""),
            "role_slot_id": str(binding.get("role_slot_id") or ""),
            "gate": dict(binding.get("gate") or {}),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def pending_status(
        *,
        candidate_task: Any,
        decision: OrganizationWorkflowCompletionDecision,
    ) -> None:
        """Project a denied completion into a non-terminal review state."""

        candidate_task.status = "waiting_for_review"
        candidate_task.status_reason_code = ORGANIZATION_WORKFLOW_WAITING_REASON
        verification = dict(getattr(candidate_task, "verification_status", None) or {})
        verification["organization_workflow_gate"] = {
            "schema": "organization_workflow_gate_status.v1",
            "status": "waiting_for_review",
            "reason_code": decision.reason_code or ORGANIZATION_WORKFLOW_WAITING_REASON,
            "binding_digest": decision.binding_digest,
        }
        candidate_task.verification_status = verification
        details = dict(getattr(candidate_task, "status_reason_details", None) or {})
        details["organization_workflow_gate"] = {
            "schema": "organization_workflow_gate_status.v1",
            "requested_status": "completed",
            "effective_status": "waiting_for_review",
            "reason_code": decision.reason_code or ORGANIZATION_WORKFLOW_WAITING_REASON,
            "binding_digest": decision.binding_digest,
        }
        candidate_task.status_reason_details = details

    @classmethod
    def _approval_request_error(
        cls,
        *,
        request: Any,
        task: Any,
        binding: Mapping[str, Any],
        binding_digest: str,
        now: float,
    ) -> str | None:
        if request is None:
            return "organization_workflow_gate_approval_missing"
        if (
            request.status != "granted"
            or request.tool_name != ORGANIZATION_WORKFLOW_APPROVAL_TOOL
            or not str(request.decided_by or "").strip()
            or str(request.decided_by or "") == "auto_policy"
            or not isinstance(request.decided_at, (int, float))
            or isinstance(request.decided_at, bool)
            or not math.isfinite(float(request.decided_at))
            or float(request.decided_at) <= 0
            or (request.expires_at is not None and float(request.expires_at) < now)
        ):
            return "organization_workflow_gate_approval_not_granted"
        expected_scope = (
            str(getattr(task, "tenant_id", "") or ""),
            str(getattr(task, "project_id", "") or ""),
            str(getattr(task, "organization_id", "") or ""),
            str(getattr(task, "id", "") or ""),
            str(getattr(task, "goal_id", "") or ""),
        )
        actual_scope = (
            str(request.tenant_id or ""),
            str(request.project_id or ""),
            str(request.organization_id or ""),
            str(request.task_id or ""),
            str(request.goal_id or ""),
        )
        if actual_scope != expected_scope:
            return "organization_workflow_gate_approval_scope_mismatch"
        if str(request.target_fingerprint or "") != binding_digest:
            return "organization_workflow_gate_approval_workflow_drift"
        arguments = dict(request.canonical_arguments or {})
        assignment_id = str(arguments.get("approval_assignment_id") or "")
        if not assignment_id or arguments != cls.approval_arguments(
            task=task,
            binding=binding,
            approval_assignment_id=assignment_id,
        ):
            return "organization_workflow_gate_approval_binding_mismatch"
        return None

    @staticmethod
    def _verification_record_error(
        *,
        record: Any,
        task: Any,
    ) -> str | None:
        """Require the latest Hub verification result, never body gate flags."""

        if record is None:
            return "organization_workflow_gate_verification_missing"
        if str(record.task_id or "") != str(getattr(task, "id", "") or ""):
            return "organization_workflow_gate_verification_task_mismatch"
        if dict(record.spec or {}) != dict(getattr(task, "verification_spec", None) or {}):
            return "organization_workflow_gate_verification_spec_drift"
        results = dict(record.results or {})
        if (
            str(record.verification_type or "") != "quality_gate"
            or record.status != "passed"
            or results.get("quality_gates_passed") is not True
            or results.get("final_passed") is not True
        ):
            return "organization_workflow_gate_verification_not_passed"
        return None

    @staticmethod
    def _approval_assignment_error(
        *,
        session: Any,
        assignment: Any,
        request: Any,
        task: Any,
        binding: Mapping[str, Any],
    ) -> str | None:
        if assignment is None or assignment.lifecycle != "active":
            return "organization_workflow_gate_approval_assignment_inactive"
        scope = (
            str(getattr(task, "tenant_id", "") or ""),
            str(getattr(task, "project_id", "") or ""),
            str(getattr(task, "organization_id", "") or ""),
        )
        if (
            assignment.tenant_id,
            assignment.project_id,
            assignment.organization_id,
        ) != scope:
            return "organization_workflow_gate_approval_assignment_scope_mismatch"
        principal_id = str(dict(assignment.assignment_metadata or {}).get("principal_id") or "")
        if not principal_id or principal_id != str(request.decided_by or ""):
            return "organization_workflow_gate_approval_principal_mismatch"
        slot = session.get(
            OrganizationRoleSlotDB,
            assignment.role_slot_id,
        )
        gate = dict(binding.get("gate") or {})
        approval_role_ref = str(gate.get("approval_role_ref") or "")
        if (
            slot is None
            or slot.lifecycle != "active"
            or slot.tenant_id != scope[0]
            or slot.project_id != scope[1]
            or slot.organization_id != scope[2]
            or f"{slot.role_template_key}@{slot.role_template_version}" != approval_role_ref
        ):
            return "organization_workflow_gate_approval_role_mismatch"
        return None

    @staticmethod
    def _independence_error(
        *,
        session: Any,
        task: Any,
        approval_principal_id: str,
    ) -> str | None:
        context = dict(getattr(task, "worker_execution_context", None) or {})
        routing = context.get("organization_routing")
        if not isinstance(routing, Mapping):
            return "organization_workflow_gate_execution_assignment_missing"
        execution_assignment_id = str(routing.get("selected_assignment_id") or "")
        execution_assignment = session.get(
            OrganizationRoleAssignmentDB,
            execution_assignment_id,
        )
        if (
            execution_assignment is None
            or execution_assignment.lifecycle != "active"
            or execution_assignment.tenant_id != str(getattr(task, "tenant_id", "") or "")
            or execution_assignment.project_id != str(getattr(task, "project_id", "") or "")
            or execution_assignment.organization_id != str(getattr(task, "organization_id", "") or "")
            or execution_assignment.role_slot_id != str(getattr(task, "role_slot_id", "") or "")
        ):
            return "organization_workflow_gate_execution_assignment_inactive"
        execution_principal_id = str(dict(execution_assignment.assignment_metadata or {}).get("principal_id") or "")
        if not execution_principal_id:
            return "organization_workflow_gate_execution_principal_missing"
        if execution_principal_id == approval_principal_id:
            return "organization_workflow_gate_independent_principal_required"
        return None

    @classmethod
    def _binding_error(
        cls,
        *,
        task: Any,
        candidate: Any,
        binding: Mapping[str, Any],
    ) -> str | None:
        gate = binding.get("gate")
        if (
            binding.get("schema") != cls.BINDING_SCHEMA
            or not isinstance(gate, Mapping)
            or gate.get("required") is not True
            or not str(gate.get("approval_role_ref") or "")
            or not isinstance(gate.get("independent_principal_required"), bool)
            or not isinstance(gate.get("acceptance_checks"), list)
        ):
            return "organization_workflow_gate_binding_invalid"
        fields = {
            "organization_id": getattr(task, "organization_id", None),
            "team_unit_id": getattr(task, "unit_id", None),
            "team_id": getattr(task, "team_id", None),
            "role_slot_id": getattr(task, "role_slot_id", None),
        }
        if any(
            not str(expected or "") or str(binding.get(field) or "") != str(expected or "")
            for field, expected in fields.items()
        ):
            return "organization_workflow_gate_task_binding_mismatch"
        for field in (
            "definition_revision",
            "workflow_ref",
            "workflow_content_hash",
            "step_id",
        ):
            if not str(binding.get(field) or ""):
                return "organization_workflow_gate_binding_invalid"
        verification = dict(getattr(candidate, "verification_spec", None) or {})
        if (
            verification.get("acceptance_checks") != gate.get("acceptance_checks")
            or verification.get("approval_role_ref") != gate.get("approval_role_ref")
            or verification.get("independent_principal_required") != gate.get("independent_principal_required")
            or verification.get("failure_policy") != binding.get("failure_policy")
        ):
            return "organization_workflow_gate_verification_binding_mismatch"
        return None

    @staticmethod
    def _workflow_binding(task: Any) -> dict[str, Any] | None:
        context = dict(getattr(task, "worker_execution_context", None) or {})
        raw = context.get("organization_workflow_step_binding")
        return dict(raw) if isinstance(raw, Mapping) else None

    @classmethod
    def _required_binding(cls, task: Any) -> dict[str, Any] | None:
        binding = cls._workflow_binding(task)
        gate = dict((binding or {}).get("gate") or {})
        return binding if gate.get("required") is True else None

    @staticmethod
    def _approval_ref(task: Any) -> dict[str, Any] | None:
        verification = dict(getattr(task, "verification_status", None) or {})
        raw = verification.get(ORGANIZATION_WORKFLOW_APPROVAL_REF_FIELD)
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"schema", "approval_request_id"}
            or raw.get("schema") != ORGANIZATION_WORKFLOW_APPROVAL_REF_SCHEMA
            or not str(raw.get("approval_request_id") or "")
        ):
            return None
        return dict(raw)

    @staticmethod
    def _status(task: Any) -> str:
        return str(getattr(task, "status", "") or "").strip().lower()

    @staticmethod
    def _denied(
        reason_code: str,
        binding_digest: str,
        *,
        approval_request_id: str | None = None,
    ) -> OrganizationWorkflowCompletionDecision:
        return OrganizationWorkflowCompletionDecision(
            applicable=True,
            allowed=False,
            reason_code=reason_code,
            binding_digest=binding_digest,
            approval_request_id=approval_request_id,
        )


organization_workflow_completion_policy_service = OrganizationWorkflowCompletionPolicyService()


__all__ = [
    "ORGANIZATION_WORKFLOW_APPROVAL_REF_FIELD",
    "ORGANIZATION_WORKFLOW_APPROVAL_REF_SCHEMA",
    "ORGANIZATION_WORKFLOW_APPROVAL_TOOL",
    "ORGANIZATION_WORKFLOW_WAITING_REASON",
    "OrganizationWorkflowCompletionDecision",
    "OrganizationWorkflowCompletionPolicyService",
    "organization_workflow_completion_policy_service",
]
