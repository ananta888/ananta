"""HTTP-facing composition for the Organization planning control plane.

The component intentionally contains no Flask objects.  It binds trusted
operator identity to membership/grants, delegates all state transitions to
the narrow Planning services, and projects their normalized persistence into
the Angular/TUI read model.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_
from sqlmodel import Session, select

from agent.config import settings
from agent.db_models import (
    GoalDB,
    OrganizationInstanceDB,
    PlanningArtifactRevisionDB,
    WorkerTaskProposalDB,
)
from agent.services.approval_request_service import (
    ApprovalDecisionError,
    ApprovalRequestService,
)
from agent.services.category_to_planning_track_service import (
    CategoryToPlanningTrackService,
)
from agent.services.organization_category_research_readiness_service import (
    OrganizationCategoryResearchReadinessService,
)
from agent.services.organization_category_research_service import (
    OrganizationCategoryResearchService,
)
from agent.services.organization_definition_catalog_service import (
    get_organization_definition_catalog,
)
from agent.services.organization_membership_service import (
    OrganizationAccessPrincipal,
    OrganizationMembershipService,
)
from agent.services.organization_planning_dispatch_service import (
    PlanningDispatchOutboxService,
)
from agent.services.organization_reference_workflow_service import (
    OrganizationReferenceWorkflowService,
)
from agent.services.organization_track_planning_service import (
    OrganizationTrackPlanningService,
)
from agent.services.planning_artifact_transition_service import (
    PlanningArtifactTransitionService,
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.planning_hierarchy_projection_service import (
    PlanningHierarchyProjectionService,
)
from agent.services.planning_principal_identity_service import (
    canonical_planning_actor_id,
)
from agent.services.planning_task_materialization_service import (
    PlanningTaskMaterializationService,
)
from agent.services.worker_task_proposal_decision_service import (
    WorkerTaskProposalDecisionService,
)
from agent.services.worker_task_proposal_result_adapter import (
    authoritative_assignment_scope,
)

_ROOT = Path(__file__).resolve().parents[2]
_TRACK_PROMPT_HASH = hashlib.sha256((_ROOT / "prompts" / "planning" / "track_planning.j2").read_bytes()).hexdigest()


class OrganizationPlanningCompositionError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


class _PlanningCursorCodec:
    _PREFIX = "opc1"

    def __init__(self, secret: str) -> None:
        self._secret = hashlib.sha256(str(secret or "").encode("utf-8")).digest()

    def encode(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        created_at: float,
        goal_id: str,
    ) -> str:
        claims = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "organization_id": organization_id,
            "created_at": float(created_at),
            "goal_id": goal_id,
        }
        payload = self._encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        return f"{self._PREFIX}.{payload}.{self._signature(payload)}"

    def decode(
        self,
        cursor: str,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> tuple[float, str]:
        parts = str(cursor or "").split(".")
        if len(parts) != 3 or parts[0] != self._PREFIX:
            self._invalid()
        payload, signature = parts[1], parts[2]
        if not hmac.compare_digest(signature, self._signature(payload)):
            self._invalid()
        try:
            claims = json.loads(self._decode(payload).decode("utf-8"))
            created_at = float(claims["created_at"])
            goal_id = str(claims["goal_id"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._invalid()
        if (
            str(claims.get("tenant_id") or "") != tenant_id
            or str(claims.get("project_id") or "") != project_id
            or str(claims.get("organization_id") or "") != organization_id
            or not goal_id
        ):
            self._invalid()
        return created_at, goal_id

    def _signature(self, payload: str) -> str:
        digest = hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest()
        return self._encode(digest)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    @staticmethod
    def _invalid() -> None:
        raise OrganizationPlanningCompositionError(
            "organization_planning_cursor_invalid",
            status_code=400,
        )


class OrganizationPlanningComposition:
    """Compose scoped reads and Hub-owned planning/proposal transitions."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        membership_service: OrganizationMembershipService | None = None,
        projection_service: PlanningHierarchyProjectionService | None = None,
        transition_service: PlanningArtifactTransitionService | None = None,
        proposal_decision_service: WorkerTaskProposalDecisionService | None = None,
        approval_service: ApprovalRequestService | None = None,
        category_research_service: OrganizationCategoryResearchService | None = None,
        category_research_readiness_service: OrganizationCategoryResearchReadinessService | None = None,
        track_derivation_service: CategoryToPlanningTrackService | None = None,
        track_planning_service: OrganizationTrackPlanningService | None = None,
        materialization_service: PlanningTaskMaterializationService | None = None,
        dispatch_service: PlanningDispatchOutboxService | None = None,
        reference_workflow_service: OrganizationReferenceWorkflowService | None = None,
        cursor_codec: _PlanningCursorCodec | None = None,
    ) -> None:
        self._session_factory = session_factory or self._default_session
        self._membership = membership_service or OrganizationMembershipService()
        self._projection = projection_service or PlanningHierarchyProjectionService()
        self._transitions = transition_service or PlanningArtifactTransitionService()
        self._proposal_decisions = proposal_decision_service or WorkerTaskProposalDecisionService()
        self._approvals = approval_service or ApprovalRequestService()
        self._category_research_readiness = (
            category_research_readiness_service
            or OrganizationCategoryResearchReadinessService(
                session_factory=self._session_factory,
            )
        )
        self._category_research = category_research_service or OrganizationCategoryResearchService(
            readiness_service=self._category_research_readiness,
        )
        self._track_derivation = track_derivation_service or CategoryToPlanningTrackService()
        self._track_planning = track_planning_service or OrganizationTrackPlanningService(
            track_derivation_service=self._track_derivation
        )
        self._materialization = materialization_service or PlanningTaskMaterializationService()
        self._dispatch = dispatch_service or PlanningDispatchOutboxService()
        self._reference_workflows = reference_workflow_service or (
            OrganizationReferenceWorkflowService(catalog=get_organization_definition_catalog())
        )
        self._cursors = cursor_codec or _PlanningCursorCodec(settings.secret_key)

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def get_planning(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> dict[str, Any]:
        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant=None,
        )
        return self._read_model(
            principal=principal,
            organization=organization,
            cursor=cursor,
            page_size=page_size,
        )

    def transition_artifact(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        artifact_revision_id: str,
        operation: str,
        expected_revision: int,
        expected_digest: str,
        approval_request_id: str | None,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], int]:
        operation_value = str(operation or "")
        grant_kind = {
            "promote": "approval:category_promote",
            "adopt": "approval:track_adopt",
        }.get(operation_value)
        expected_type = {
            "promote": "planning_category_todo",
            "adopt": "planning_track",
        }.get(operation_value)
        service_operation = {
            "promote": "category_promote",
            "adopt": "track_adopt",
        }.get(operation_value)
        if not grant_kind or not expected_type or not service_operation:
            raise OrganizationPlanningCompositionError(
                "organization_planning_operation_invalid",
                status_code=404,
            )
        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant=grant_kind,
        )
        revision = self._artifact(
            organization=organization,
            artifact_revision_id=artifact_revision_id,
            expected_type=expected_type,
        )
        self._require_revision_precondition(
            current_revision=revision.revision,
            current_digest=revision.content_digest,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        context = self._operation_context(principal=principal, organization=organization)
        approval_id = str(approval_request_id or "").strip()
        if not approval_id:
            approval = self._transitions.request_operation_approval(
                context=context,
                artifact_revision_id=revision.id,
                expected_digest=revision.content_digest,
                expected_policy_hash=revision.policy_hash,
                operation=service_operation,
            )
            approval_id = str(approval.id)
            approval_status = str(approval.status or "")
            if approval_status == "pending":
                response = self._read_model(
                    principal=principal,
                    organization=organization,
                    cursor=None,
                    page_size=20,
                )
                response["pending_approval"] = {
                    "operation": service_operation,
                    "approval_request_id": approval_id,
                    "artifact_revision_id": revision.id,
                    "revision": str(revision.revision),
                    "digest": revision.content_digest,
                    "status": approval_status,
                }
                return response, 202
            if approval_status not in {"granted", "consumed"}:
                raise PlanningTransitionError(f"planning_approval_not_granted:{approval_status}")

        transition = (
            self._transitions.promote_category if operation_value == "promote" else self._transitions.adopt_track
        )
        receipt = transition(
            context=context,
            artifact_revision_id=revision.id,
            expected_digest=revision.content_digest,
            expected_policy_hash=revision.policy_hash,
            approval_request_id=approval_id,
            approval_required=True,
            idempotency_key=idempotency_key,
        )
        response = self._read_model(
            principal=principal,
            organization=organization,
            cursor=None,
            page_size=20,
        )
        response["transition"] = {
            **dict(receipt),
            "revision": str(revision.revision),
            "digest": revision.content_digest,
        }
        return response, 200

    def create_category_research(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        goal_id: str,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
        catalog_binding: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant="planning:category_research",
        )
        return self._category_research.create_task(
            context=self._operation_context(
                principal=principal,
                organization=organization,
            ),
            goal_id=goal_id,
            unit_id=unit_id,
            team_id=team_id,
            role_slot_id=role_slot_id,
            catalog_binding=catalog_binding,
            idempotency_key=idempotency_key,
        )

    def get_category_research_readiness(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        goal_id: str,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
        catalog_task_id: str,
    ) -> dict[str, Any]:
        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant=None,
        )
        return self._category_research_readiness.evaluate(
            context=self._operation_context(
                principal=principal,
                organization=organization,
            ),
            goal_id=goal_id,
            unit_id=unit_id,
            team_id=team_id,
            role_slot_id=role_slot_id,
            catalog_task_id=catalog_task_id,
        )

    def accept_category_research_result(
        self,
        *,
        source_task_id: str,
        assignment_id: str,
        capability_claims: Mapping[str, Any],
        raw_output: str,
        raw_output_digest: str,
        idempotency_key: str,
        runtime_artifact_hashes: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        return self._category_research.accept_result(
            source_task_id=source_task_id,
            assignment_id=assignment_id,
            capability_claims=capability_claims,
            raw_output=raw_output,
            raw_output_digest=raw_output_digest,
            idempotency_key=idempotency_key,
            runtime_artifact_hashes=runtime_artifact_hashes,
        )

    def derive_tracks(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        category_revision_id: str,
        expected_revision: int,
        expected_digest: str,
        expected_policy_hash: str,
        track_candidates: list[Mapping[str, Any]],
        exclusions: Mapping[str, str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant="planning:track_derive",
        )
        category = self._artifact(
            organization=organization,
            artifact_revision_id=category_revision_id,
            expected_type="planning_category_todo",
        )
        self._require_revision_precondition(
            current_revision=category.revision,
            current_digest=category.content_digest,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        if category.policy_hash != str(expected_policy_hash or ""):
            raise PlanningTransitionError("category_policy_hash_stale")
        return self._track_derivation.derive_tracks(
            category_revision_id=category.id,
            expected_category_digest=category.content_digest,
            expected_policy_hash=category.policy_hash,
            track_candidates=track_candidates,
            exclusions=exclusions,
            worker_id=None,
            assignment_id=None,
            dispatch_lease_id=None,
            prompt_hash=_TRACK_PROMPT_HASH,
            principal_id=principal.principal_id,
            idempotency_key=idempotency_key,
        )

    def create_track_planning_task(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        category_revision_id: str,
        expected_revision: int,
        expected_digest: str,
        expected_policy_hash: str,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
        source_category_item_ids: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant="planning:track_derive",
        )
        category = self._artifact(
            organization=organization,
            artifact_revision_id=category_revision_id,
            expected_type="planning_category_todo",
        )
        self._require_revision_precondition(
            current_revision=category.revision,
            current_digest=category.content_digest,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        if category.policy_hash != str(expected_policy_hash or ""):
            raise PlanningTransitionError("category_policy_hash_stale")
        return self._track_planning.create_task(
            context=self._operation_context(
                principal=principal,
                organization=organization,
            ),
            category_revision_id=category.id,
            expected_category_digest=category.content_digest,
            expected_policy_hash=category.policy_hash,
            unit_id=unit_id,
            team_id=team_id,
            role_slot_id=role_slot_id,
            source_category_item_ids=source_category_item_ids,
            idempotency_key=idempotency_key,
        )

    def accept_track_planning_result(
        self,
        *,
        source_task_id: str,
        assignment_id: str,
        capability_claims: Mapping[str, Any],
        carrier: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._track_planning.accept_result(
            source_task_id=source_task_id,
            assignment_id=assignment_id,
            capability_claims=capability_claims,
            carrier=carrier,
            idempotency_key=idempotency_key,
        )

    def preview_reference_workflow(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        category_revision_id: str,
        expected_revision: int,
        expected_digest: str,
        expected_policy_hash: str,
        workflow_key: str,
        workflow_version: int,
        goal: str,
        source_category_item_ids: list[str],
    ) -> dict[str, Any]:
        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant=None,
        )
        category = self._reference_category(
            organization=organization,
            category_revision_id=category_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=expected_policy_hash,
            source_category_item_ids=source_category_item_ids,
        )
        candidate = self._reference_workflows.preview_track_candidate(
            tenant_id=organization.tenant_id,
            project_id=organization.project_id,
            organization_id=organization.organization_id,
            workflow_key=workflow_key,
            workflow_version=workflow_version,
            goal=goal,
            source_category_item_ids=source_category_item_ids,
            owner=canonical_planning_actor_id(principal.principal_id),
        )
        return {
            **candidate,
            "category_revision_id": category.id,
            "category_revision": str(category.revision),
            "category_digest": category.content_digest,
            "category_policy_hash": category.policy_hash,
            "materialized_task_ids": [],
        }

    def derive_reference_workflow(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        category_revision_id: str,
        expected_revision: int,
        expected_digest: str,
        expected_policy_hash: str,
        workflow_key: str,
        workflow_version: int,
        goal: str,
        source_category_item_ids: list[str],
        exclusions: Mapping[str, str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        preview = self.preview_reference_workflow(
            principal=principal,
            organization_id=organization_id,
            category_revision_id=category_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=expected_policy_hash,
            workflow_key=workflow_key,
            workflow_version=workflow_version,
            goal=goal,
            source_category_item_ids=source_category_item_ids,
        )
        result = self.derive_tracks(
            principal=principal,
            organization_id=organization_id,
            category_revision_id=category_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=expected_policy_hash,
            track_candidates=[
                {
                    "artifact_id": str(preview["artifact_id"]),
                    "payload": dict(preview["payload"]),
                }
            ],
            exclusions=exclusions,
            idempotency_key=idempotency_key,
        )
        return {
            **result,
            "workflow_ref": preview["workflow_ref"],
            "task_count": preview["task_count"],
            "gate_count": preview["gate_count"],
        }

    def _reference_category(
        self,
        *,
        organization: OrganizationInstanceDB,
        category_revision_id: str,
        expected_revision: int,
        expected_digest: str,
        expected_policy_hash: str,
        source_category_item_ids: list[str],
    ) -> PlanningArtifactRevisionDB:
        category = self._artifact(
            organization=organization,
            artifact_revision_id=category_revision_id,
            expected_type="planning_category_todo",
        )
        self._require_revision_precondition(
            current_revision=category.revision,
            current_digest=category.content_digest,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        if category.status != "promoted":
            raise PlanningTransitionError("category_revision_not_promoted")
        if category.policy_hash != str(expected_policy_hash or ""):
            raise PlanningTransitionError("category_policy_hash_stale")
        normalized = [str(value or "").strip() for value in source_category_item_ids]
        if len(normalized) != 1 or not normalized[0]:
            # The generic Track lineage validator allows many Category items,
            # but a fixed reference DAG cannot truthfully infer a per-item
            # mapping. One workflow therefore derives from one exact item.
            raise PlanningTransitionError("organization_reference_workflow_single_category_item_required")
        known_ids = {
            str(item.get("id") or "")
            for group in list(category.payload.get("categories") or [])
            if isinstance(group, Mapping)
            for item in list(group.get("items") or [])
            if isinstance(item, Mapping)
        }
        if normalized[0] not in known_ids:
            raise PlanningTransitionError("organization_reference_workflow_category_item_unknown")
        return category

    def materialize_track(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        track_revision_id: str,
        expected_revision: int,
        expected_digest: str,
        expected_policy_hash: str,
        approval_request_id: str | None,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], int]:
        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant="approval:track_materialize",
        )
        track = self._artifact(
            organization=organization,
            artifact_revision_id=track_revision_id,
            expected_type="planning_track",
        )
        self._require_revision_precondition(
            current_revision=track.revision,
            current_digest=track.content_digest,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        if track.policy_hash != str(expected_policy_hash or ""):
            raise PlanningTransitionError("planning_policy_hash_stale")
        context = self._operation_context(
            principal=principal,
            organization=organization,
        )
        approval_id = str(approval_request_id or "").strip()
        if not approval_id:
            approval = self._transitions.request_operation_approval(
                context=context,
                artifact_revision_id=track.id,
                expected_digest=track.content_digest,
                expected_policy_hash=track.policy_hash,
                operation="track_materialize",
            )
            approval_id = str(approval.id)
            if str(approval.status or "") == "pending":
                return (
                    {
                        "status": "pending_approval",
                        "approval_request_id": approval_id,
                        "track_revision_id": track.id,
                        "revision": str(track.revision),
                        "digest": track.content_digest,
                        "materialized_task_ids": [],
                    },
                    202,
                )
            if str(approval.status or "") not in {"granted", "consumed"}:
                raise PlanningTransitionError("planning_approval_not_granted")
        receipt = self._materialization.materialize(
            context=context,
            track_revision_id=track.id,
            expected_track_digest=track.content_digest,
            expected_policy_hash=track.policy_hash,
            approval_request_id=approval_id,
            idempotency_key=idempotency_key,
        )
        return receipt, 200

    def dispatch_next(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        track_revision_id: str,
        plan_task_id: str,
        idempotency_key: str,
        requested_worker_id: str | None,
        pump: bool,
    ) -> tuple[dict[str, Any], int]:
        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant="planning:track_dispatch",
        )
        context = self._operation_context(
            principal=principal,
            organization=organization,
        )
        intent = self._materialization.claim_next(
            context=context,
            track_revision_id=track_revision_id,
            plan_task_id=plan_task_id,
            idempotency_key=idempotency_key,
            requested_worker_id=requested_worker_id,
        )
        if not pump:
            return intent, 202
        receipt = self._dispatch.pump_intent(
            context=context,
            dispatch_intent_id=str(intent["dispatch_intent_id"]),
            pump_owner=f"hub:{principal.principal_id}",
        )
        return receipt, 200 if receipt.get("status") == "dispatched" else 202

    def retry_dispatch(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        dispatch_intent_id: str,
        pump: bool,
    ) -> tuple[dict[str, Any], int]:
        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant="planning:track_dispatch",
        )
        context = self._operation_context(
            principal=principal,
            organization=organization,
        )
        receipt = self._dispatch.retry(
            context=context,
            dispatch_intent_id=dispatch_intent_id,
        )
        if pump:
            receipt = self._dispatch.pump_intent(
                context=context,
                dispatch_intent_id=dispatch_intent_id,
                pump_owner=f"hub:{principal.principal_id}",
            )
        return receipt, 200 if receipt.get("status") == "dispatched" else 202

    def pump_dispatches(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        limit: int,
    ) -> dict[str, Any]:
        """Pump due intents under the same scoped Hub authority as execute-next."""

        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant="planning:track_dispatch",
        )
        receipts = self._dispatch.pump_due(
            context=self._operation_context(
                principal=principal,
                organization=organization,
            ),
            pump_owner=f"hub:{principal.principal_id}",
            limit=limit,
        )
        return {
            "organization_id": organization.organization_id,
            "processed_count": len(receipts),
            "dispatches": receipts,
        }

    def decide_proposal(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        proposal_id: str,
        operation: str,
        expected_revision: int,
        expected_digest: str,
    ) -> dict[str, Any]:
        if operation not in {"approve", "reject"}:
            raise OrganizationPlanningCompositionError(
                "organization_planning_operation_invalid",
                status_code=404,
            )
        organization = self._organization(
            principal=principal,
            organization_id=organization_id,
            mutation_grant="approval:proposal_amend",
        )
        proposal = self._proposal(
            organization=organization,
            proposal_id=proposal_id,
        )
        self._require_revision_precondition(
            current_revision=proposal.proposal_revision,
            current_digest=proposal.envelope_digest,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        if principal.principal_id == proposal.proposing_worker_id:
            raise OrganizationPlanningCompositionError(
                "proposal_self_approval_forbidden",
                status_code=403,
            )
        context = self._operation_context(principal=principal, organization=organization)
        if operation == "reject":
            decision = self._proposal_decisions.reject(
                proposal_id=proposal.proposal_id,
                context=context,
                source_goal_id=proposal.source_goal_id,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
        else:
            assignment, policy = authoritative_assignment_scope(source_task_id=proposal.source_task_id)
            decision = self._proposal_decisions.classify(
                proposal_id=proposal.proposal_id,
                context=context,
                assignment=assignment,
                current_role_policy=policy,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            if str(decision.get("state") or "") == "needs_approval":
                request_id = str(decision.get("approval_request_id") or "")
                self._grant_proposal_approval(request_id=request_id, actor=principal.principal_id)
                decision = self._proposal_decisions.classify(
                    proposal_id=proposal.proposal_id,
                    context=context,
                    assignment=assignment,
                    current_role_policy=policy,
                    approval_request_id=request_id,
                    expected_revision=expected_revision,
                    expected_digest=expected_digest,
                )
        response = self._read_model(
            principal=principal,
            organization=organization,
            cursor=None,
            page_size=20,
        )
        response["proposal_decision"] = self._normalize_decision(decision)
        return response

    def _organization(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        mutation_grant: str | None,
    ) -> OrganizationInstanceDB:
        with self._session_factory() as session:
            organization = session.exec(
                select(OrganizationInstanceDB).where(
                    OrganizationInstanceDB.organization_id == str(organization_id or ""),
                    OrganizationInstanceDB.tenant_id == str(principal.tenant_id or ""),
                    *((OrganizationInstanceDB.project_id == principal.project_id,) if principal.project_id else ()),
                )
            ).one_or_none()
        if organization is None:
            self._not_found()
        authorization = (
            self._membership.can_mutate(
                principal=principal,
                tenant_id=organization.tenant_id,
                project_id=organization.project_id,
                organization_id=organization.organization_id,
                grant_kind=mutation_grant,
            )
            if mutation_grant
            else self._membership.can_view(
                principal=principal,
                tenant_id=organization.tenant_id,
                project_id=organization.project_id,
                organization_id=organization.organization_id,
            )
        )
        if not authorization:
            self._not_found()
        return organization

    def _artifact(
        self,
        *,
        organization: OrganizationInstanceDB,
        artifact_revision_id: str,
        expected_type: str,
    ) -> PlanningArtifactRevisionDB:
        with self._session_factory() as session:
            row = session.exec(
                select(PlanningArtifactRevisionDB).where(
                    PlanningArtifactRevisionDB.id == str(artifact_revision_id or ""),
                    PlanningArtifactRevisionDB.tenant_id == organization.tenant_id,
                    PlanningArtifactRevisionDB.project_id == organization.project_id,
                    PlanningArtifactRevisionDB.organization_id == organization.organization_id,
                    PlanningArtifactRevisionDB.artifact_type == expected_type,
                )
            ).one_or_none()
        if row is None:
            self._not_found()
        return row

    def _proposal(
        self,
        *,
        organization: OrganizationInstanceDB,
        proposal_id: str,
    ) -> WorkerTaskProposalDB:
        with self._session_factory() as session:
            row = session.exec(
                select(WorkerTaskProposalDB).where(
                    WorkerTaskProposalDB.proposal_id == str(proposal_id or ""),
                    WorkerTaskProposalDB.tenant_id == organization.tenant_id,
                    WorkerTaskProposalDB.project_id == organization.project_id,
                    WorkerTaskProposalDB.organization_id == organization.organization_id,
                )
            ).one_or_none()
        if row is None:
            self._not_found()
        return row

    def _read_model(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization: OrganizationInstanceDB,
        cursor: str | None,
        page_size: int,
    ) -> dict[str, Any]:
        limit = max(1, min(int(page_size), 50))
        with self._session_factory() as session:
            statement = select(GoalDB).where(
                GoalDB.tenant_id == organization.tenant_id,
                GoalDB.project_id == organization.project_id,
                GoalDB.organization_id == organization.organization_id,
                or_(GoalDB.parent_goal_id.is_(None), GoalDB.goal_kind == "organization"),
            )
            if cursor:
                created_at, goal_id = self._cursors.decode(
                    cursor,
                    tenant_id=organization.tenant_id,
                    project_id=organization.project_id,
                    organization_id=organization.organization_id,
                )
                statement = statement.where(
                    or_(
                        GoalDB.created_at < created_at,
                        and_(GoalDB.created_at == created_at, GoalDB.id < goal_id),
                    )
                )
            goals = list(
                session.exec(
                    statement.order_by(GoalDB.created_at.desc(), GoalDB.id.desc()).limit(limit + 1)  # type: ignore[attr-defined]
                ).all()
            )
            has_more = len(goals) > limit
            goals = goals[:limit]
            goal_ids = [row.id for row in goals]
            revisions = (
                list(
                    session.exec(
                        select(PlanningArtifactRevisionDB)
                        .where(
                            PlanningArtifactRevisionDB.tenant_id == organization.tenant_id,
                            PlanningArtifactRevisionDB.project_id == organization.project_id,
                            PlanningArtifactRevisionDB.organization_id == organization.organization_id,
                            PlanningArtifactRevisionDB.goal_id.in_(goal_ids),
                        )
                        .order_by(PlanningArtifactRevisionDB.created_at.asc())  # type: ignore[attr-defined]
                    ).all()
                )
                if goal_ids
                else []
            )
            proposals = (
                list(
                    session.exec(
                        select(WorkerTaskProposalDB)
                        .where(
                            WorkerTaskProposalDB.tenant_id == organization.tenant_id,
                            WorkerTaskProposalDB.project_id == organization.project_id,
                            WorkerTaskProposalDB.organization_id == organization.organization_id,
                            WorkerTaskProposalDB.source_goal_id.in_(goal_ids),
                        )
                        .order_by(WorkerTaskProposalDB.created_at.desc())  # type: ignore[attr-defined]
                    ).all()
                )
                if goal_ids
                else []
            )

        context = self._operation_context(principal=principal, organization=organization)
        projections = {goal.id: self._projection.project_goal(context=context, goal_id=goal.id) for goal in goals}
        next_cursor = None
        if has_more and goals:
            tail = goals[-1]
            next_cursor = self._cursors.encode(
                tenant_id=organization.tenant_id,
                project_id=organization.project_id,
                organization_id=organization.organization_id,
                created_at=tail.created_at,
                goal_id=tail.id,
            )
        return {
            "organization_id": organization.organization_id,
            "definition_revision": organization.definition_revision,
            "nodes": self._planning_nodes(
                goals=goals,
                revisions=revisions,
                projections=projections,
            ),
            "proposals": [self._proposal_view(row) for row in proposals],
            "next_cursor": next_cursor,
        }

    @staticmethod
    def _planning_nodes(
        *,
        goals: list[GoalDB],
        revisions: list[PlanningArtifactRevisionDB],
        projections: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        for goal in goals:
            projection = dict(projections.get(goal.id) or {})
            nodes.append(
                {
                    "id": goal.id,
                    "kind": "goal",
                    "label": str(goal.goal or goal.summary or goal.id),
                    "status": str(projection.get("organization_goal_status") or goal.status or "planning"),
                    "parent_id": None,
                }
            )

        runtime_tracks: dict[str, dict[str, Any]] = {}
        for projection in projections.values():
            for track in list(dict(projection).get("tracks") or []):
                if isinstance(track, Mapping):
                    runtime_tracks[str(track.get("track_artifact_revision_id") or "")] = dict(
                        track.get("payload") or {}
                    )

        for revision in revisions:
            payload = dict(revision.payload or {})
            if revision.artifact_type == "planning_category_todo":
                nodes.append(
                    {
                        "id": revision.id,
                        "kind": "category_todo",
                        "label": str(payload.get("project") or f"Category-Todo r{revision.revision}"),
                        "status": {
                            "valid": "validated",
                            "failed": "invalid",
                        }.get(revision.status, revision.status),
                        "revision": str(revision.revision),
                        "digest": revision.content_digest,
                        "parent_id": revision.goal_id,
                        "artifact_id": revision.artifact_id,
                    }
                )
                continue
            if revision.artifact_type != "planning_track":
                continue
            payload = runtime_tracks.get(revision.id, payload)
            nodes.append(
                {
                    "id": revision.id,
                    "kind": "planning_track",
                    "label": str(payload.get("track") or f"Planning Track r{revision.revision}"),
                    "status": revision.status,
                    "revision": str(revision.revision),
                    "digest": revision.content_digest,
                    "parent_id": revision.parent_revision_id or revision.goal_id,
                    "artifact_id": revision.artifact_id,
                    "source_category_item_ids": list(revision.source_category_item_ids or []),
                }
            )
            task_parent: dict[str, str] = {}
            for milestone in list(payload.get("milestones") or []):
                if not isinstance(milestone, Mapping):
                    continue
                source_id = str(milestone.get("id") or "")
                if not source_id:
                    continue
                node_id = f"{revision.id}:milestone:{source_id}"
                nodes.append(
                    {
                        "id": node_id,
                        "kind": "milestone",
                        "label": str(milestone.get("title") or source_id),
                        "status": str(milestone.get("status") or "todo"),
                        "parent_id": revision.id,
                        "source_category_item_ids": list(milestone.get("source_category_item_ids") or []),
                    }
                )
                for task_id in list(milestone.get("task_ids") or []):
                    task_parent.setdefault(str(task_id), node_id)
            for task in list(payload.get("tasks") or []):
                if not isinstance(task, Mapping):
                    continue
                source_id = str(task.get("id") or "")
                if not source_id:
                    continue
                nodes.append(
                    {
                        "id": f"{revision.id}:task:{source_id}",
                        "kind": "task",
                        "label": str(task.get("title") or source_id),
                        "status": str(task.get("status") or "todo"),
                        "parent_id": task_parent.get(source_id, revision.id),
                        "source_category_item_ids": list(task.get("source_category_item_ids") or []),
                    }
                )
        return nodes

    @staticmethod
    def _proposal_view(proposal: WorkerTaskProposalDB) -> dict[str, Any]:
        payload = dict(dict(proposal.envelope or {}).get("payload") or {})
        decision = dict(proposal.decision or {})

        def hints(name: str) -> str | None:
            values = [str(value) for value in list(payload.get(name) or []) if str(value)]
            return ", ".join(values) or None

        status = {
            "submitted": "pending",
            "materialized": "accepted_as_plan_amendment",
        }.get(proposal.state, proposal.state)
        return {
            "proposal_id": proposal.proposal_id,
            "revision": str(proposal.proposal_revision),
            "digest": proposal.envelope_digest,
            "proposal_revision": proposal.proposal_revision,
            "proposal_digest": proposal.envelope_digest,
            "payload_digest": proposal.payload_digest,
            "source_task_id": proposal.source_task_id,
            "proposer_role_slot_id": proposal.role_slot_id,
            "proposing_role_template_ref": proposal.proposing_role_template_ref,
            "status": status,
            "state": proposal.state,
            "policy_hash": proposal.policy_hash,
            "reason_code": proposal.reason_code,
            "target_role_hint": hints("suggested_role_refs"),
            "target_team_hint": hints("suggested_team_refs"),
            "target_agent_hint": hints("suggested_agent_refs"),
            "selected_role_slot_id": decision.get("selected_role_slot_id"),
            "selected_team_id": decision.get("selected_team_id"),
            "selected_agent_id": decision.get("selected_agent_id"),
            "approval_id": proposal.approval_request_id,
            "approval_request_id": proposal.approval_request_id,
            "source_category_item_ids": list(proposal.source_category_item_ids or []),
            "category_artifact_revision_id": decision.get("category_artifact_revision_id"),
            "category_revision": decision.get("category_revision"),
            "category_digest": decision.get("category_digest"),
            "source_track_artifact_revision_id": decision.get("source_track_artifact_revision_id"),
            "source_track_revision": decision.get("source_track_revision"),
            "source_track_digest": decision.get("source_track_digest"),
            "amendment_track_artifact_revision_id": proposal.amendment_track_revision_id,
            "amendment_track_revision": decision.get("amendment_track_revision"),
            "amendment_track_digest": decision.get("amendment_track_digest"),
        }

    def _grant_proposal_approval(self, *, request_id: str, actor: str) -> None:
        request = self._approvals.get_request(request_id)
        if request is None:
            raise PlanningTransitionError("planning_approval_not_found")
        if str(request.status or "") == "pending":
            self._approvals.decide_request(
                request.id,
                decision="granted",
                decided_by=actor,
                reason="organization_planning_proposal_approved",
            )
        elif str(request.status or "") != "granted":
            raise ApprovalDecisionError(
                f"request_already_{request.status}",
                409,
            )

    @staticmethod
    def _normalize_decision(decision: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(decision)
        result.setdefault("revision", str(result.get("proposal_revision") or ""))
        result.setdefault("digest", str(result.get("proposal_digest") or ""))
        return result

    @staticmethod
    def _require_revision_precondition(
        *,
        current_revision: int,
        current_digest: str,
        expected_revision: int,
        expected_digest: str,
    ) -> None:
        if int(current_revision) != int(expected_revision) or str(current_digest or "") != str(expected_digest or ""):
            raise OrganizationPlanningCompositionError(
                "organization_planning_precondition_failed",
                status_code=412,
            )

    @staticmethod
    def _operation_context(
        *,
        principal: OrganizationAccessPrincipal,
        organization: OrganizationInstanceDB,
    ) -> PlanningOperationContext:
        return PlanningOperationContext.hub_admin(
            subject_id=principal.principal_id,
            tenant_id=organization.tenant_id,
            project_id=organization.project_id,
            organization_id=organization.organization_id,
        )

    @staticmethod
    def _not_found() -> None:
        raise OrganizationPlanningCompositionError(
            "organization_planning_not_found",
            status_code=404,
        )


@lru_cache(maxsize=1)
def get_organization_planning_composition() -> OrganizationPlanningComposition:
    return OrganizationPlanningComposition()


__all__ = [
    "OrganizationPlanningComposition",
    "OrganizationPlanningCompositionError",
    "get_organization_planning_composition",
]
