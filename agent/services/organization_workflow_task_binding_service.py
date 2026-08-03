"""Revision-bound Organization workflow metadata for Hub runtime Tasks."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from agent.db_models import OrganizationInstanceDB
from agent.services.planning_artifact_transition_service import PlanningTransitionError


class OrganizationWorkflowTaskBindingPort(Protocol):
    """Narrow materializer dependency for immutable workflow contracts."""

    def current_definition_revision(self, *, session: Any, track: Any) -> str: ...

    def contracts(
        self,
        *,
        track: Any,
        tasks: Sequence[Mapping[str, Any]],
        current_definition_revision: str | None,
    ) -> dict[str, dict[str, Any]]: ...


class OrganizationWorkflowTaskBindingService:
    """Validate and project one workflow-step contract into Task JSON fields.

    The service has no queue or dispatch authority.  It only normalizes the
    immutable binding that the Hub materializer persists and later replays.
    """

    SCHEMA = "organization_workflow_step_binding.v1"

    def current_definition_revision(self, *, session: Any, track: Any) -> str:
        organization = session.get(OrganizationInstanceDB, track.organization_id)
        if (
            organization is None
            or organization.tenant_id != track.tenant_id
            or organization.project_id != track.project_id
            or organization.organization_id != track.organization_id
        ):
            raise PlanningTransitionError("planning_task_workflow_binding_organization_missing")
        revision = str(organization.definition_revision or "").strip()
        if not revision:
            raise PlanningTransitionError("planning_task_workflow_binding_revision_invalid")
        return revision

    def contracts(
        self,
        *,
        track: Any,
        tasks: Sequence[Mapping[str, Any]],
        current_definition_revision: str | None,
    ) -> dict[str, dict[str, Any]]:
        return {
            str(task.get("id") or ""): {
                "workflow_binding": self.workflow_step_binding(
                    track=track,
                    task=task,
                    current_definition_revision=current_definition_revision,
                ),
                "verification_spec": self.verification_spec(task),
            }
            for task in tasks
        }

    def workflow_step_binding(
        self,
        *,
        track: Any,
        task: Mapping[str, Any],
        current_definition_revision: str | None,
    ) -> dict[str, Any] | None:
        if "organization_workflow_step_binding" not in task:
            return None
        raw = task.get("organization_workflow_step_binding")
        if not isinstance(raw, Mapping):
            raise PlanningTransitionError("planning_task_workflow_binding_invalid")
        binding = copy.deepcopy(dict(raw))
        required_fields = {
            "schema",
            "organization_id",
            "definition_revision",
            "workflow_ref",
            "workflow_content_hash",
            "step_id",
            "team_unit_id",
            "team_id",
            "role_slot_id",
            "gate",
            "handoff_ref",
            "failure_policy",
        }
        if set(binding) != required_fields or binding.get("schema") != self.SCHEMA:
            raise PlanningTransitionError("planning_task_workflow_binding_invalid")
        string_fields = (
            "organization_id",
            "definition_revision",
            "workflow_ref",
            "workflow_content_hash",
            "step_id",
            "team_unit_id",
            "team_id",
            "role_slot_id",
            "failure_policy",
        )
        if any(not isinstance(binding.get(field), str) or not str(binding[field]).strip() for field in string_fields):
            raise PlanningTransitionError("planning_task_workflow_binding_invalid")
        workflow_hash = str(binding["workflow_content_hash"])
        if len(workflow_hash) != 64 or any(character not in "0123456789abcdef" for character in workflow_hash):
            raise PlanningTransitionError("planning_task_workflow_binding_invalid")
        self._require_versioned_ref(str(binding["workflow_ref"]))
        handoff_ref = binding.get("handoff_ref")
        if handoff_ref is not None:
            if not isinstance(handoff_ref, str) or not handoff_ref.strip():
                raise PlanningTransitionError("planning_task_workflow_binding_invalid")
            self._require_versioned_ref(handoff_ref)
        if binding["failure_policy"] not in {"block", "manual"}:
            raise PlanningTransitionError("planning_task_workflow_binding_invalid")

        raw_gate = binding.get("gate")
        gate_fields = {
            "required",
            "acceptance_checks",
            "approval_role_ref",
            "independent_principal_required",
        }
        if not isinstance(raw_gate, Mapping) or set(raw_gate) != gate_fields:
            raise PlanningTransitionError("planning_task_workflow_binding_invalid")
        gate = dict(raw_gate)
        checks = gate.get("acceptance_checks")
        approval_role_ref = gate.get("approval_role_ref")
        if (
            not isinstance(gate.get("required"), bool)
            or not isinstance(gate.get("independent_principal_required"), bool)
            or not isinstance(checks, list)
            or any(not isinstance(value, str) or not value.strip() for value in checks)
            or (
                approval_role_ref is not None
                and (not isinstance(approval_role_ref, str) or not approval_role_ref.strip())
            )
        ):
            raise PlanningTransitionError("planning_task_workflow_binding_invalid")

        task_gate = task.get("gate")
        if not isinstance(task_gate, bool):
            raise PlanningTransitionError("planning_task_workflow_binding_invalid")
        payload = dict(track.payload or {})
        topology_binding = self._task_binding(task)
        if (
            binding["organization_id"] != track.organization_id
            or payload.get("organization_id") != binding["organization_id"]
            or payload.get("definition_revision") != binding["definition_revision"]
            or payload.get("workflow_ref") != binding["workflow_ref"]
            or current_definition_revision != binding["definition_revision"]
            or topology_binding["unit_id"] != binding["team_unit_id"]
            or topology_binding["team_id"] != binding["team_id"]
            or topology_binding["role_slot_id"] != binding["role_slot_id"]
            or task_gate != gate["required"]
            or (str(task.get("handoff_ref") or "").strip() or None) != handoff_ref
        ):
            raise PlanningTransitionError("planning_task_workflow_binding_conflict")
        verification_spec = self.verification_spec(task)
        self._require_matching_verification(
            verification_spec=verification_spec,
            gate=gate,
            failure_policy=str(binding["failure_policy"]),
        )
        return binding

    @staticmethod
    def verification_spec(task: Mapping[str, Any]) -> dict[str, Any]:
        raw = task.get("verification_spec")
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            raise PlanningTransitionError("planning_task_verification_spec_invalid")
        return copy.deepcopy(dict(raw))

    @staticmethod
    def _task_binding(task: Mapping[str, Any]) -> dict[str, str]:
        nested_value = task.get("organization_binding")
        nested = dict(nested_value) if isinstance(nested_value, Mapping) else {}
        binding = {
            "unit_id": str(task.get("unit_id") or nested.get("unit_id") or "").strip(),
            "team_id": str(task.get("team_id") or nested.get("team_id") or "").strip(),
            "role_slot_id": str(task.get("role_slot_id") or nested.get("role_slot_id") or "").strip(),
        }
        if any(not value for value in binding.values()):
            raise PlanningTransitionError("planning_task_organization_binding_required")
        return binding

    @staticmethod
    def _require_versioned_ref(value: str) -> None:
        parts = value.rsplit("@", 1)
        if len(parts) != 2 or not parts[0] or not parts[1].isdigit() or int(parts[1]) < 1:
            raise PlanningTransitionError("planning_task_workflow_binding_invalid")

    @staticmethod
    def _require_matching_verification(
        *,
        verification_spec: Mapping[str, Any],
        gate: Mapping[str, Any],
        failure_policy: str,
    ) -> None:
        verification_checks = verification_spec.get("acceptance_checks")
        verification_independence = verification_spec.get("independent_principal_required")
        approval_role_ref = verification_spec.get("approval_role_ref")
        if (
            not isinstance(verification_checks, list)
            or any(not isinstance(value, str) or not value.strip() for value in verification_checks)
            or not isinstance(verification_independence, bool)
            or (approval_role_ref is not None and not isinstance(approval_role_ref, str))
            or verification_checks != gate["acceptance_checks"]
            or approval_role_ref != gate["approval_role_ref"]
            or verification_independence != gate["independent_principal_required"]
            or str(verification_spec.get("failure_policy") or "") != failure_policy
        ):
            raise PlanningTransitionError("planning_task_workflow_verification_conflict")


__all__ = [
    "OrganizationWorkflowTaskBindingPort",
    "OrganizationWorkflowTaskBindingService",
]
