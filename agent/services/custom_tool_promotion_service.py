"""HDE-015/HDE-019: promotion lifecycle for custom tool proposals.

State machine: ``pending -> validated|validation_failed ->
approval_required -> approved -> active`` (plus ``disabled`` /
``rejected``). No tool becomes active without schema validation, test
validation and a digest-bound approval — or an explicit admin override,
which is audited as such. Any content change produces a new
``proposal_digest`` and thereby invalidates earlier validation results
and grants: activation re-checks that the validated digest still
matches the proposal digest and that the approval was granted for
exactly this digest.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agent.common.audit import audit_hub_direct_event
from agent.services.custom_tool_proposal_service import (
    STATUS_ACTIVE,
    STATUS_APPROVAL_REQUIRED,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_VALIDATED,
    STATUS_VALIDATION_FAILED,
    CustomToolProposalService,
)
from agent.services.custom_tool_validation_service import CustomToolValidationService
from agent.services.dynamic_tool_registry_service import DynamicToolRegistryService

AUDIT_CUSTOM_TOOL_PROMOTION = "custom_tool_promotion_transition"

# The approval request is digest-bound: tool_name is the promotion
# action, the proposal digest rides as target_fingerprint, so a changed
# proposal can never reuse an old grant.
_PROMOTION_APPROVAL_TOOL = "custom_tool.promote"


class CustomToolPromotionError(ValueError):
    pass


class CustomToolPromotionService:
    """Drives proposals through validation, approval and activation."""

    def __init__(
        self,
        *,
        data_root: Path | str | None = None,
        proposal_service: CustomToolProposalService | None = None,
        validation_service: CustomToolValidationService | None = None,
        registry: DynamicToolRegistryService | None = None,
    ) -> None:
        self._proposals = proposal_service or CustomToolProposalService(data_root)
        self._validation = validation_service or CustomToolValidationService(data_root)
        self._registry = registry or DynamicToolRegistryService(data_root)

    # -- transitions ----------------------------------------------------------

    def validate(self, digest: str) -> dict[str, Any]:
        proposal = self._require_proposal(digest)
        if str(proposal.get("status")) not in {STATUS_PENDING, STATUS_VALIDATION_FAILED, STATUS_VALIDATED}:
            raise CustomToolPromotionError(f"validate_not_allowed_from:{proposal.get('status')}")
        passed, report_ref, _report = self._validation.validate_proposal(proposal)
        updated = self._proposals.update_proposal(
            digest,
            {
                "status": STATUS_VALIDATED if passed else STATUS_VALIDATION_FAILED,
                "validated_digest": digest if passed else None,
                "validation_report_ref": report_ref,
            },
        )
        self._audit(digest, proposal, "validated" if passed else "validation_failed")
        return updated or proposal

    def request_approval(self, digest: str, *, agent_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        proposal = self._require_proposal(digest)
        if str(proposal.get("status")) != STATUS_VALIDATED:
            raise CustomToolPromotionError(f"approval_requires_validated_proposal:{proposal.get('status')}")
        from agent.services.approval_request_service import get_approval_request_service

        request = get_approval_request_service().create_pending_request(
            task_id=str(proposal.get("source_task_id") or "") or None,
            tool_name=_PROMOTION_APPROVAL_TOOL,
            arguments={"name": proposal.get("name"), "proposal_digest": digest},
            target_fingerprint=digest,
            risk_class=str(proposal.get("risk_class") or "execution"),
            scope={"source": "custom_tool_promotion", "tool_name": proposal.get("name")},
            agent_cfg=agent_cfg,
        )
        updates: dict[str, Any] = {"approval_request_id": request.id}
        if request.status == "granted":
            updates["status"] = STATUS_APPROVED
            updates["approval_status"] = "granted"
        else:
            updates["status"] = STATUS_APPROVAL_REQUIRED
        updated = self._proposals.update_proposal(digest, updates)
        self._audit(digest, proposal, str(updates["status"]))
        return updated or proposal

    def refresh_approval(self, digest: str) -> dict[str, Any]:
        """Pull the approval decision into the proposal state."""
        proposal = self._require_proposal(digest)
        request_id = str(proposal.get("approval_request_id") or "")
        if not request_id:
            raise CustomToolPromotionError("no_approval_request_for_proposal")
        from agent.services.approval_request_service import get_approval_request_service

        request = get_approval_request_service().get_request(request_id)
        if request is None:
            raise CustomToolPromotionError("approval_request_not_found")
        if str(request.target_fingerprint or "") != digest:
            raise CustomToolPromotionError("approval_digest_mismatch")
        if request.status == "granted":
            updated = self._proposals.update_proposal(digest, {"status": STATUS_APPROVED, "approval_status": "granted"})
            self._audit(digest, proposal, STATUS_APPROVED)
            return updated or proposal
        if request.status == "denied":
            updated = self._proposals.update_proposal(digest, {"status": STATUS_REJECTED, "approval_status": "denied"})
            self._audit(digest, proposal, STATUS_REJECTED)
            return updated or proposal
        return proposal

    def activate(self, digest: str, *, actor: str = "system", admin_override: bool = False) -> dict[str, Any]:
        """Write the approved proposal into the dynamic registry.

        Hard requirements (HDE-015): validated digest matches, approval
        granted for exactly this digest — or an explicit, audited admin
        override.
        """
        proposal = self._require_proposal(digest)
        status = str(proposal.get("status"))
        if admin_override:
            if str(proposal.get("validated_digest") or "") != digest:
                raise CustomToolPromotionError("admin_override_requires_validation")
        else:
            if status != STATUS_APPROVED:
                raise CustomToolPromotionError(f"activate_requires_approved_proposal:{status}")
            if str(proposal.get("validated_digest") or "") != digest:
                raise CustomToolPromotionError("validated_digest_mismatch")
            if str(proposal.get("approval_status")) != "granted":
                raise CustomToolPromotionError("approval_not_granted")

        record = self._registry.store_promoted_tool(
            name=str(proposal.get("name")),
            spec={key: value for key, value in proposal.items() if key not in {"status", "approval_request_id"}},
            proposal_digest=digest,
            validated_digest=digest,
            validation_report_ref=proposal.get("validation_report_ref"),
            approval_status="granted",
        )
        self._proposals.update_proposal(digest, {"status": STATUS_ACTIVE})
        self._audit(digest, proposal, STATUS_ACTIVE, actor=actor, admin_override=admin_override)
        return record

    def reject(self, digest: str, *, reason: str = "") -> dict[str, Any]:
        proposal = self._require_proposal(digest)
        updated = self._proposals.update_proposal(digest, {"status": STATUS_REJECTED, "approval_status": "denied"})
        self._audit(digest, proposal, STATUS_REJECTED, reason_code=str(reason or "") or None)
        return updated or proposal

    def disable(self, name: str) -> dict[str, Any] | None:
        record = self._registry.set_status(name, "disabled")
        audit_hub_direct_event(AUDIT_CUSTOM_TOOL_PROMOTION, tool_name=name, status="disabled")
        return record

    def reactivate(self, name: str) -> dict[str, Any] | None:
        record = self._registry.set_status(name, "active")
        audit_hub_direct_event(AUDIT_CUSTOM_TOOL_PROMOTION, tool_name=name, status="active")
        return record

    def rollback(self, name: str, version: int) -> dict[str, Any]:
        record = self._registry.rollback(name, version)
        audit_hub_direct_event(AUDIT_CUSTOM_TOOL_PROMOTION, tool_name=name, status=f"rolled_back_to_v{version}")
        return record

    # -- helpers --------------------------------------------------------------

    def _require_proposal(self, digest: str) -> dict[str, Any]:
        proposal = self._proposals.get_proposal(digest)
        if proposal is None:
            raise CustomToolPromotionError(f"unknown_proposal:{digest}")
        return proposal

    @staticmethod
    def _audit(digest: str, proposal: dict[str, Any], status: str, *, actor: str | None = None, admin_override: bool = False, reason_code: str | None = None) -> None:
        audit_hub_direct_event(
            AUDIT_CUSTOM_TOOL_PROMOTION,
            tool_name=str(proposal.get("name") or ""),
            status=status,
            reason_code=reason_code,
            proposal_digest=digest,
            actor=actor,
            admin_override=admin_override or None,
            at=time.time(),
        )


custom_tool_promotion_service: CustomToolPromotionService | None = None


def get_custom_tool_promotion_service() -> CustomToolPromotionService:
    global custom_tool_promotion_service
    if custom_tool_promotion_service is None:
        custom_tool_promotion_service = CustomToolPromotionService()
    return custom_tool_promotion_service
