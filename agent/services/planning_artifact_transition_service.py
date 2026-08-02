from __future__ import annotations

import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from agent.db_models import PlanningOperationReceiptDB
from agent.services.approval_request_service import (
    ApprovalRequestService,
    canonical_approval_intent_key,
)
from agent.services.planning_category_contract_service import (
    category_schema_hash,
    stable_planning_digest,
)
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
    planning_scope_lock,
)
from agent.services.planning_principal_identity_service import (
    planning_separation_of_duties_reason,
)
from agent.services.planning_track_contract_service import planning_contract_hash
from agent.services.planning_track_pipeline_service import (
    validate_planning_track_with_details,
)

CATEGORY_PROMOTE_TOOL = "planning.category.promote"
TRACK_ADOPT_TOOL = "planning.track.adopt"
TRACK_MATERIALIZE_TOOL = "planning.track.materialize"
PROPOSAL_AMEND_TOOL = "planning.proposal.amend"

_OPERATION_TOOL = {
    "category_promote": CATEGORY_PROMOTE_TOOL,
    "track_adopt": TRACK_ADOPT_TOOL,
    "track_materialize": TRACK_MATERIALIZE_TOOL,
    "proposal_amend": PROPOSAL_AMEND_TOOL,
}


class PlanningTransitionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class PlanningOperationContext:
    subject_id: str
    tenant_id: str
    project_id: str
    organization_id: str
    hub_owned: bool
    roles: frozenset[str]
    allowed_operations: frozenset[str] = frozenset()

    @classmethod
    def hub_admin(
        cls,
        *,
        subject_id: str,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> PlanningOperationContext:
        return cls(
            subject_id=subject_id,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            hub_owned=True,
            roles=frozenset({"organization_admin"}),
        )


class PlanningArtifactTransitionService:
    """Hub-only state transitions; it owns no task materialization logic."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
        approval_service: ApprovalRequestService | None = None,
    ) -> None:
        self._uow_factory = uow_factory or PlanningControlUnitOfWork
        self._approvals = approval_service or ApprovalRequestService()

    def request_operation_approval(
        self,
        *,
        context: PlanningOperationContext,
        artifact_revision_id: str,
        expected_digest: str,
        expected_policy_hash: str,
        operation: str,
        ttl_seconds: int | None = None,
    ) -> Any:
        tool_name = self._tool(operation)
        self._authorize(context=context, operation=operation)
        with planning_scope_lock(f"planning-approval:{artifact_revision_id}"), self._uow_factory() as uow:
            assert uow.planning is not None and uow.session is not None
            uow.planning.acquire_scope_lock(f"planning-approval:{artifact_revision_id}")
            revision = uow.planning.get_revision(artifact_revision_id)
            self._validate_scope(context=context, revision=revision)
            if revision is None:
                raise PlanningTransitionError("planning_revision_not_found")
            if revision.content_digest != str(expected_digest or ""):
                raise PlanningTransitionError("planning_revision_digest_mismatch")
            if revision.policy_hash != str(expected_policy_hash or ""):
                raise PlanningTransitionError("planning_policy_hash_stale")
            self._validate_revision_integrity(revision=revision, operation=operation)
            intent = self._intent_key(revision=revision, operation=operation)
            arguments = self._approval_arguments(revision=revision, operation=operation)
            return self._approvals.ensure_passive_request_in_session(
                uow.session,
                tool_name=tool_name,
                approval_intent_key=intent,
                tenant_id=revision.tenant_id,
                project_id=revision.project_id,
                organization_id=revision.organization_id,
                goal_id=revision.goal_id,
                arguments=arguments,
                target_fingerprint=revision.content_digest,
                scope={
                    "approval_class": "planning_control_plane",
                    "tenant_id": revision.tenant_id,
                    "project_id": revision.project_id,
                    "organization_id": revision.organization_id,
                    "goal_id": revision.goal_id,
                    "artifact_revision_id": revision.id,
                    "artifact_digest": revision.content_digest,
                    "policy_hash": revision.policy_hash,
                    "operation": operation,
                    "passive_transition": True,
                },
                ttl_seconds=int(ttl_seconds or 3600),
            )

    def promote_category(
        self,
        *,
        context: PlanningOperationContext,
        artifact_revision_id: str,
        expected_digest: str,
        expected_policy_hash: str,
        approval_request_id: str | None,
        approval_required: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._transition(
            context=context,
            artifact_revision_id=artifact_revision_id,
            artifact_type="planning_category_todo",
            expected_status="valid",
            next_status="promoted",
            expected_digest=expected_digest,
            expected_policy_hash=expected_policy_hash,
            approval_request_id=approval_request_id,
            approval_required=approval_required,
            idempotency_key=idempotency_key,
            operation="category_promote",
        )

    def adopt_track(
        self,
        *,
        context: PlanningOperationContext,
        artifact_revision_id: str,
        expected_digest: str,
        expected_policy_hash: str,
        approval_request_id: str | None,
        approval_required: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._transition(
            context=context,
            artifact_revision_id=artifact_revision_id,
            artifact_type="planning_track",
            expected_status="valid",
            next_status="adopted",
            expected_digest=expected_digest,
            expected_policy_hash=expected_policy_hash,
            approval_request_id=approval_request_id,
            approval_required=approval_required,
            idempotency_key=idempotency_key,
            operation="track_adopt",
        )

    def reject_revision(
        self,
        *,
        context: PlanningOperationContext,
        artifact_revision_id: str,
        expected_statuses: Collection[str] = ("valid", "promoted", "adopted"),
    ) -> dict[str, Any]:
        self._authorize(context=context, operation="reject")
        with planning_scope_lock(f"planning-reject:{artifact_revision_id}"), self._uow_factory() as uow:
            assert uow.planning is not None
            uow.planning.acquire_scope_lock(f"planning-reject:{artifact_revision_id}")
            revision = uow.planning.get_revision(artifact_revision_id, for_update=True)
            self._validate_scope(context=context, revision=revision)
            if revision is None:
                raise PlanningTransitionError("planning_revision_not_found")
            if revision.status not in set(expected_statuses):
                raise PlanningTransitionError("planning_revision_transition_conflict")
            revision.status = "rejected"
            revision.updated_at = time.time()
            if revision.artifact_type == "planning_category_todo":
                uow.planning.mark_derived_tracks_stale(category_revision_id=revision.id)
            assert uow.session is not None
            uow.session.add(revision)
        self._audit("planning_revision_rejected", revision, context.subject_id)
        return {"artifact_revision_id": revision.id, "status": "rejected"}

    def _transition(
        self,
        *,
        context: PlanningOperationContext,
        artifact_revision_id: str,
        artifact_type: str,
        expected_status: str,
        next_status: str,
        expected_digest: str,
        expected_policy_hash: str,
        approval_request_id: str | None,
        approval_required: bool,
        idempotency_key: str,
        operation: str,
    ) -> dict[str, Any]:
        self._authorize(context=context, operation=operation)
        if not str(idempotency_key or "").strip():
            raise PlanningTransitionError("planning_idempotency_key_required")
        with planning_scope_lock(f"planning-transition:{artifact_revision_id}"), self._uow_factory() as uow:
            assert uow.planning is not None and uow.session is not None
            uow.planning.acquire_scope_lock(f"planning-transition:{artifact_revision_id}")
            revision = uow.planning.get_revision(artifact_revision_id, for_update=True)
            self._validate_scope(context=context, revision=revision)
            if revision is None or revision.artifact_type != artifact_type:
                raise PlanningTransitionError("planning_revision_not_found")
            if revision.content_digest != str(expected_digest or ""):
                raise PlanningTransitionError("planning_revision_digest_mismatch")
            if revision.policy_hash != str(expected_policy_hash or ""):
                raise PlanningTransitionError("planning_policy_hash_stale")
            self._validate_revision_integrity(revision=revision, operation=operation)
            intent = self._intent_key(revision=revision, operation=operation)
            existing = uow.planning.get_receipt_by_intent(
                approval_intent_key=intent,
                operation=operation,
            )
            if existing is not None:
                return self._receipt_response(existing)
            if revision.status != expected_status:
                raise PlanningTransitionError("planning_revision_transition_conflict")

            if artifact_type == "planning_category_todo" and not bool(
                dict(revision.validation_result or {}).get("promotable")
            ):
                raise PlanningTransitionError("planning_category_not_promotable")
            if artifact_type == "planning_track":
                parent = uow.planning.get_revision(str(revision.parent_revision_id or ""), for_update=True)
                if parent is None or parent.status != "promoted":
                    raise PlanningTransitionError("planning_category_not_promoted")
                expected_parent_digest = str(
                    dict(revision.execution_provenance or {}).get("source_category_digest") or ""
                )
                if parent.content_digest != expected_parent_digest:
                    raise PlanningTransitionError("planning_category_lineage_stale")

            approval_id = str(approval_request_id or "").strip()
            if approval_required and not approval_id:
                raise PlanningTransitionError("planning_approval_required")
            if approval_id:
                grant = self._approvals.consume_bound_request_in_session(
                    uow.session,
                    request_id=approval_id,
                    tool_name=self._tool(operation),
                    approval_intent_key=intent,
                    tenant_id=revision.tenant_id,
                    project_id=revision.project_id,
                    goal_id=revision.goal_id,
                    organization_id=revision.organization_id,
                )
                sod_reason = planning_separation_of_duties_reason(
                    revision=revision,
                    decided_by=grant.decided_by,
                )
                if sod_reason is not None:
                    raise PlanningTransitionError(sod_reason)
            elif approval_required:
                raise PlanningTransitionError("planning_approval_required")
            else:
                approval_id = "policy:not-required"

            transitioned = uow.planning.compare_and_set_status(
                revision_id=revision.id,
                expected_status=expected_status,
                next_status=next_status,
                expected_digest=expected_digest,
                expected_policy_hash=expected_policy_hash,
                values={
                    "approval_request_id": approval_id,
                    "updated_at": time.time(),
                    "promoted_at" if next_status == "promoted" else "adopted_at": time.time(),
                },
            )
            if not transitioned:
                raise PlanningTransitionError("planning_revision_transition_conflict")

            superseded = uow.planning.supersede_other_revisions(
                artifact_id=revision.artifact_id,
                keep_revision_id=revision.id,
                statuses=(next_status,),
            )
            if next_status == "promoted":
                previous = [
                    row
                    for row in uow.planning.list_revisions(
                        goal_id=revision.goal_id,
                        organization_id=revision.organization_id,
                        artifact_type="planning_category_todo",
                    )
                    if row.artifact_id == revision.artifact_id and row.id != revision.id
                ]
                for old_revision in previous:
                    uow.planning.mark_derived_tracks_stale(category_revision_id=old_revision.id)

            receipt = PlanningOperationReceiptDB(
                tenant_id=revision.tenant_id,
                project_id=revision.project_id,
                organization_id=revision.organization_id,
                goal_id=revision.goal_id,
                artifact_revision_id=revision.id,
                operation=operation,
                approval_intent_key=intent,
                approval_request_id=approval_id,
                idempotency_key=str(idempotency_key),
                artifact_digest=revision.content_digest,
                policy_hash=revision.policy_hash,
                details={"next_status": next_status, "superseded_revision_count": superseded},
            )
            uow.planning.add_receipt(receipt)
        self._audit(f"planning_{operation}", revision, context.subject_id)
        return self._receipt_response(receipt)

    @staticmethod
    def _approval_arguments(*, revision: Any, operation: str) -> dict[str, str]:
        return {
            "operation": operation,
            "artifact_revision_id": revision.id,
            "artifact_digest": revision.content_digest,
            "policy_hash": revision.policy_hash,
            "goal_id": revision.goal_id,
            "organization_id": revision.organization_id,
        }

    @staticmethod
    def _intent_key(*, revision: Any, operation: str) -> str:
        return canonical_approval_intent_key(
            tenant_id=revision.tenant_id,
            project_id=revision.project_id,
            organization_id=revision.organization_id,
            goal_id=revision.goal_id,
            operation=operation,
            artifact_revision_id=revision.id,
            artifact_digest=revision.content_digest,
            policy_hash=revision.policy_hash,
        )

    @staticmethod
    def _authorize(*, context: PlanningOperationContext, operation: str) -> None:
        if not context.hub_owned:
            raise PlanningTransitionError("planning_hub_authority_required")
        if "organization_admin" not in context.roles and operation not in context.allowed_operations:
            raise PlanningTransitionError("planning_organization_admin_required")

    @staticmethod
    def _validate_scope(*, context: PlanningOperationContext, revision: Any | None) -> None:
        if revision is None:
            return
        if (
            revision.tenant_id != context.tenant_id
            or revision.project_id != context.project_id
            or revision.organization_id != context.organization_id
        ):
            raise PlanningTransitionError("planning_scope_forbidden")

    @staticmethod
    def _tool(operation: str) -> str:
        tool = _OPERATION_TOOL.get(str(operation or ""))
        if not tool:
            raise PlanningTransitionError("planning_operation_unknown")
        return tool

    @staticmethod
    def _validate_revision_integrity(*, revision: Any, operation: str) -> None:
        if stable_planning_digest(dict(revision.payload or {})) != revision.content_digest:
            raise PlanningTransitionError("planning_revision_payload_digest_stale")
        if operation == "category_promote":
            if revision.artifact_type != "planning_category_todo":
                raise PlanningTransitionError("planning_operation_artifact_type_mismatch")
            if revision.schema_hash != category_schema_hash():
                raise PlanningTransitionError("planning_category_schema_hash_stale")
            if not bool(dict(revision.validation_result or {}).get("promotable")):
                raise PlanningTransitionError("planning_category_not_promotable")
            return
        if operation in {"track_adopt", "track_materialize"}:
            if revision.artifact_type != "planning_track":
                raise PlanningTransitionError("planning_operation_artifact_type_mismatch")
            if revision.schema_hash != planning_contract_hash():
                raise PlanningTransitionError("planning_track_schema_hash_stale")
            if not bool(dict(revision.validation_result or {}).get("valid")) or validate_planning_track_with_details(
                dict(revision.payload or {})
            ):
                raise PlanningTransitionError("planning_track_not_valid")

    @staticmethod
    def _receipt_response(receipt: PlanningOperationReceiptDB) -> dict[str, Any]:
        return {
            "receipt_id": receipt.id,
            "artifact_revision_id": receipt.artifact_revision_id,
            "operation": receipt.operation,
            "status": str(dict(receipt.details or {}).get("next_status") or receipt.status),
            "approval_request_id": receipt.approval_request_id,
            "materialized_task_ids": [],
        }

    @staticmethod
    def _audit(action: str, revision: Any, actor: str) -> None:
        try:
            from agent.common.audit import log_audit

            log_audit(
                action,
                {
                    "artifact_revision_id": revision.id,
                    "artifact_type": revision.artifact_type,
                    "goal_id": revision.goal_id,
                    "organization_id": revision.organization_id,
                    "digest_prefix": str(revision.content_digest)[:12],
                    "actor": actor,
                },
            )
        except Exception:
            return


__all__ = [
    "CATEGORY_PROMOTE_TOOL",
    "PROPOSAL_AMEND_TOOL",
    "TRACK_ADOPT_TOOL",
    "TRACK_MATERIALIZE_TOOL",
    "PlanningArtifactTransitionService",
    "PlanningOperationContext",
    "PlanningTransitionError",
]
