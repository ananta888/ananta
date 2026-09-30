"""Patch preview and decision audit for Visual Process Assistant answers.

The Hub validates an assistant ``WorkflowPatch`` against the caller's current
draft, records one immutable preview audit per patch hash and applies the
Hub-owned approval policy to accept/reject decisions.  Accepted patches are
only ever applied as local editor commands.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.visual_process_assistant import (
    VisualProcessAssistantRequestDB,
    VisualProcessPatchAuditDB,
)
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.visual_process_assistant_errors import VisualProcessAssistantError
from agent.services.visual_process_assistant_records import (
    owned_context,
    owned_graph,
    owned_request,
    validated_patch_draft,
)
from agent.services.visual_process_patch_approval_policy import (
    VisualProcessPatchApprovalError,
    VisualProcessPatchApprovalPolicy,
)
from agent.services.visual_process_patch_service import VisualProcessPatchService
from ananta_contracts.visual_process_assistant import (
    EditorContextEnvelope,
    HelpResponse,
    WorkflowPatch,
)


class VisualProcessAssistantPatchGovernance:
    """Preview assistant patches and audit the principal's decision."""

    def __init__(
        self,
        *,
        patch_service: VisualProcessPatchService,
        patch_approval_policy: VisualProcessPatchApprovalPolicy,
        clock: Callable[[], float],
    ) -> None:
        self._patches = patch_service
        self._patch_approval = patch_approval_policy
        self._clock = clock

    def preview(
        self,
        *,
        principal: ChatSessionPrincipal,
        request_id: str,
        patch_payload: Mapping[str, Any] | None,
        patch_enabled: bool,
        draft_graph_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not patch_enabled:
            raise VisualProcessAssistantError("assistant_patch_feature_disabled", status_code=404)
        with Session(engine) as db:
            request_row = owned_request(db, request_id, principal)
            if request_row.status != "completed" or not request_row.response_json:
                raise VisualProcessAssistantError("assistant_patch_response_unavailable", status_code=409)
            response = HelpResponse.model_validate(request_row.response_json)
            candidate = (
                dict(patch_payload or {})
                if patch_payload
                else (response.workflow_patch.model_dump(mode="json") if response.workflow_patch is not None else None)
            )
            if candidate is None:
                raise VisualProcessAssistantError("assistant_patch_missing", status_code=404)
            patch = WorkflowPatch.model_validate(candidate)
            definition = owned_graph(db, patch.graph_id, principal)
            context = owned_context(db, request_row.prompt_context_id or request_row.context_id, principal)
            envelope = EditorContextEnvelope.model_validate(context.context_json)
            draft = validated_patch_draft(
                definition=definition,
                payload=draft_graph_payload,
            )
            if draft.definition_hash() != envelope.draft_hash:
                raise VisualProcessAssistantError(
                    "assistant_patch_context_draft_conflict",
                    status_code=409,
                )
            preview = self._patches.preview(
                graph=draft,
                patch=patch,
                allowed_operations=envelope.allowed_mutations,
            )
            audit = db.exec(
                select(VisualProcessPatchAuditDB).where(
                    VisualProcessPatchAuditDB.request_id == request_row.id,
                    VisualProcessPatchAuditDB.patch_hash == preview.patch_hash,
                )
            ).first()
            if audit is None:
                audit = VisualProcessPatchAuditDB(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject_id,
                    request_id=request_row.id,
                    graph_id=definition.id,
                    context_id=context.context_id,
                    prompt_version=request_row.prompt_version,
                    patch_hash=preview.patch_hash,
                    decision="previewed",
                    reason_codes=list(preview.policy_reason_codes),
                    result_json=preview.as_dict(),
                )
                db.add(audit)
                db.commit()
                db.refresh(audit)
            return {
                **preview.as_dict(),
                "audit_id": audit.id,
                "decision": audit.decision,
                "audit_reason_codes": list(audit.reason_codes),
            }

    def decide(
        self,
        *,
        principal: ChatSessionPrincipal,
        request_id: str,
        patch_hash: str,
        decision: str,
        confirmed: bool,
        patch_enabled: bool,
        approval_mode: str = "interactive",
        auto_approval_enabled: bool = False,
        draft_graph_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = str(decision or "").strip().lower()
        if normalized not in {"accepted", "rejected"}:
            raise VisualProcessAssistantError("assistant_patch_decision_invalid")
        if not patch_enabled:
            raise VisualProcessAssistantError("assistant_patch_feature_disabled", status_code=404)
        approval = None
        if normalized == "accepted":
            try:
                approval = self._patch_approval.authorize_acceptance(
                    mode=approval_mode,
                    confirmed=confirmed,
                    hub_auto_enabled=auto_approval_enabled,
                )
            except VisualProcessPatchApprovalError as exc:
                raise VisualProcessAssistantError(exc.reason_code, status_code=exc.status_code) from exc
        with Session(engine) as db:
            owned_request(db, request_id, principal)
            audit = db.exec(
                select(VisualProcessPatchAuditDB)
                .where(
                    VisualProcessPatchAuditDB.request_id == request_id,
                    VisualProcessPatchAuditDB.patch_hash == str(patch_hash),
                    VisualProcessPatchAuditDB.tenant_id == principal.tenant_id,
                    VisualProcessPatchAuditDB.owner_subject == principal.subject_id,
                )
                .with_for_update()
            ).first()
            if audit is None:
                raise VisualProcessAssistantError("assistant_patch_preview_not_found", status_code=404)
            if audit.decision not in {"previewed", normalized}:
                raise VisualProcessAssistantError("assistant_patch_decision_conflict", status_code=409)
            if normalized == "accepted":
                request_row = db.get(VisualProcessAssistantRequestDB, request_id)
                assert request_row is not None and request_row.response_json is not None
                response = HelpResponse.model_validate(request_row.response_json)
                if response.workflow_patch is None:
                    raise VisualProcessAssistantError("assistant_patch_missing", status_code=404)
                definition = owned_graph(db, audit.graph_id, principal)
                context = owned_context(db, audit.context_id, principal)
                envelope = EditorContextEnvelope.model_validate(context.context_json)
                draft = validated_patch_draft(
                    definition=definition,
                    payload=draft_graph_payload,
                )
                expected_draft_hash = str(audit.result_json.get("input_draft_hash") or "")
                if (
                    not expected_draft_hash
                    or draft.definition_hash() != expected_draft_hash
                    or draft.definition_hash() != envelope.draft_hash
                ):
                    raise VisualProcessAssistantError(
                        "assistant_patch_decision_draft_conflict",
                        status_code=409,
                    )
                current_preview = self._patches.preview(
                    graph=draft,
                    patch=response.workflow_patch,
                    allowed_operations=envelope.allowed_mutations,
                )
                if current_preview.patch_hash != audit.patch_hash:
                    raise VisualProcessAssistantError("assistant_patch_hash_conflict", status_code=409)
                audit.result_json = current_preview.as_dict()
                audit.reason_codes = sorted(
                    {
                        *current_preview.policy_reason_codes,
                        approval.reason_code,
                    }
                )
            else:
                audit.reason_codes = sorted({*audit.reason_codes, "patch_user_rejected"})
            audit.decision = normalized
            audit.decided_at = float(self._clock())
            db.add(audit)
            db.commit()
            return {
                "audit_id": audit.id,
                "request_id": audit.request_id,
                "patch_hash": audit.patch_hash,
                "decision": audit.decision,
                "reason_codes": list(audit.reason_codes),
                "approval_mode": approval.mode if approval is not None else "none",
                "human_intervention_required": (
                    approval.human_intervention_required if approval is not None else False
                ),
                "apply_mode": "local_editor_command_only" if normalized == "accepted" else "none",
                "preview": copy.deepcopy(audit.result_json),
            }


__all__ = ["VisualProcessAssistantPatchGovernance"]
