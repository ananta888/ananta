"""Hub-owned Category-to-Track planning task and Worker result admission.

The service deliberately separates the second planning phase from productive
Task materialization.  It creates one role-bound planner Task for one immutable
promoted Category revision, then admits only an assignment/capability-bound
result for Hub-side Track validation and persistence.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from agent.db_models import (
    GoalDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    PlanningArtifactRevisionDB,
    TaskDB,
)
from agent.services.category_to_planning_track_service import (
    CategoryToPlanningTrackService,
)
from agent.services.organization_track_planning_contract_service import (
    TRACK_PLANNING_RESULT_SCHEMA,
    required_track_category_item_ids,
    validate_track_planning_identifier,
    validate_track_planning_result_carrier,
)
from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
    planning_scope_lock,
)
from agent.services.worker_task_proposal_policy_service import (
    WorkerTaskProposalPolicyService,
)

_ROOT = Path(__file__).resolve().parents[2]
_PROMPT_PATH = _ROOT / "prompts" / "planning" / "organization_track_planning.j2"


class OrganizationTrackPlanningService:
    """Own the single delegated Track-planning phase for a Category revision."""

    def __init__(
        self,
        *,
        track_derivation_service: CategoryToPlanningTrackService | None = None,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
    ) -> None:
        self._track_derivation = track_derivation_service or CategoryToPlanningTrackService()
        self._uow_factory = uow_factory or PlanningControlUnitOfWork

    def create_task(
        self,
        *,
        context: PlanningOperationContext,
        category_revision_id: str,
        expected_category_digest: str,
        expected_policy_hash: str,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
        source_category_item_ids: Sequence[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._authorize(context)
        self._require_idempotency_key(idempotency_key)
        requested_source_ids = self._normalize_source_ids(source_category_item_ids)
        prompt_hash = hashlib.sha256(_PROMPT_PATH.read_bytes()).hexdigest()
        task_id = self._stable_id(
            "ptracktask",
            context.tenant_id,
            context.project_id,
            context.organization_id,
            category_revision_id,
        )

        with (
            planning_scope_lock(f"planning-track-task:{category_revision_id}"),
            self._uow_factory() as uow,
        ):
            assert uow.session is not None and uow.planning is not None
            uow.planning.acquire_scope_lock(f"planning-track-task:{category_revision_id}")
            category = uow.planning.get_revision(category_revision_id, for_update=True)
            self._validate_category(
                category,
                context=context,
                expected_category_digest=expected_category_digest,
                expected_policy_hash=expected_policy_hash,
            )
            assert category is not None
            required_source_ids = required_track_category_item_ids(category.payload)
            if tuple(requested_source_ids) != required_source_ids:
                raise PlanningTransitionError("track_planning_category_scope_mismatch")
            self._validate_goal_and_binding(
                uow.session,
                context=context,
                goal_id=category.goal_id,
                unit_id=unit_id,
                team_id=team_id,
                role_slot_id=role_slot_id,
            )
            request_binding = self._request_binding(
                category=category,
                unit_id=unit_id,
                team_id=team_id,
                role_slot_id=role_slot_id,
                source_category_item_ids=required_source_ids,
                prompt_hash=prompt_hash,
            )
            request_digest = self._digest(request_binding)
            existing = uow.session.get(TaskDB, task_id)
            if existing is not None:
                binding = dict(existing.worker_execution_context or {}).get("planning_track_binding")
                if not isinstance(binding, Mapping) or str(binding.get("request_digest") or "") != request_digest:
                    raise PlanningTransitionError("track_planning_task_idempotency_conflict")
                return self._task_response(existing, replayed=True)

            derived = [
                revision
                for revision in uow.planning.list_revisions(
                    goal_id=category.goal_id,
                    organization_id=category.organization_id,
                    artifact_type="planning_track",
                )
                if revision.parent_revision_id == category.id
                and revision.status not in {"rejected", "superseded", "stale"}
            ]
            if derived:
                raise PlanningTransitionError("planning_tracks_already_derived")

            task = TaskDB(
                id=task_id,
                title="Partition the promoted Category revision into Planning Tracks",
                description=(
                    "Return one closed organization_track_planning_result.v1 JSON carrier. "
                    "Each candidate payload must conform to todos/todo.track.schema.json, "
                    "cover only the bound source_category_item_ids, preserve their DAG, "
                    "and must not select workers, teams, tools, context rights, or budgets."
                ),
                status="todo",
                priority="High",
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                organization_id=context.organization_id,
                unit_id=unit_id,
                team_id=team_id,
                role_slot_id=role_slot_id,
                goal_id=category.goal_id,
                task_kind="planning_track_task",
                required_capabilities=["planning"],
                worker_execution_context={
                    "planning_track_binding": {
                        **request_binding,
                        "request_digest": request_digest,
                        "creation_idempotency_key": idempotency_key,
                        "source_category_todo": dict(category.payload),
                    },
                    "allowed_source_refs": list(category.allowed_source_refs or []),
                    "allowed_run_refs": list(category.allowed_run_refs or []),
                    "allowed_tools": [],
                    "expected_output_schema": {
                        "schema_ref": "schemas/worker/organization_track_planning_result.v1.json",
                        "payload_schema_ref": "todos/todo.track.schema.json",
                        "artifact_type": "planning_track_candidates",
                    },
                    "planning_result_callback": {
                        "schema": "organization_planning_result_callback.v1",
                        "method": "POST",
                        "path_template": (
                            "/api/worker-results/tasks/{source_task_id}/assignments/{assignment_id}/planning/tracks"
                        ),
                        "authorization": "worker_result_capability",
                    },
                    "task_proposal_policy": WorkerTaskProposalPolicyService.default_deny_policy(),
                },
                history=[
                    {
                        "timestamp": time.time(),
                        "status": "todo",
                        "event_type": "organization_track_planning_created",
                        "actor": "hub:organization_planning",
                        "details": {
                            "organization_id": context.organization_id,
                            "category_revision_id": category.id,
                            "category_digest": category.content_digest,
                            "request_digest": request_digest,
                            "source_category_item_ids": list(required_source_ids),
                        },
                    }
                ],
            )
            uow.session.add(task)
            uow.session.flush()
        return self._task_response(task, replayed=False)

    def accept_result(
        self,
        *,
        source_task_id: str,
        assignment_id: str,
        capability_claims: Mapping[str, Any],
        carrier: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_idempotency_key(idempotency_key)
        normalized = validate_track_planning_result_carrier(carrier)
        self._validate_capability_binding(
            source_task_id=source_task_id,
            assignment_id=assignment_id,
            capability_claims=capability_claims,
        )
        with self._uow_factory() as uow:
            assert uow.session is not None
            task = uow.session.get(TaskDB, source_task_id)
            if task is None or task.task_kind != "planning_track_task":
                raise PlanningTransitionError("track_planning_task_not_found")
            binding = dict(task.worker_execution_context or {}).get("planning_track_binding")
            if not isinstance(binding, Mapping):
                raise PlanningTransitionError("track_planning_binding_missing")
            bound = dict(binding)
            if normalized["category_revision_id"] != str(bound.get("category_revision_id") or "") or normalized[
                "source_category_item_ids"
            ] != list(bound.get("source_category_item_ids") or []):
                raise PlanningTransitionError("track_planning_result_binding_mismatch")
            arguments = {
                "category_revision_id": str(bound.get("category_revision_id") or ""),
                "expected_category_digest": str(bound.get("category_digest") or ""),
                "expected_policy_hash": str(bound.get("policy_hash") or ""),
                "track_candidates": list(normalized["track_candidates"]),
                "exclusions": dict(normalized["exclusions"]),
                "worker_id": str(capability_claims.get("worker_id") or ""),
                "assignment_id": assignment_id,
                "dispatch_lease_id": str(capability_claims.get("dispatch_lease_id") or ""),
                "prompt_hash": str(bound.get("prompt_hash") or ""),
                "principal_id": None,
                # A Task has one logical result even if a transport client
                # retries with a different header key after losing a response.
                "idempotency_key": f"planning-track-result:{source_task_id}",
                "source_task_id": source_task_id,
                "required_source_category_item_ids": list(normalized["source_category_item_ids"]),
                "result_payload_digest": str(normalized["payload_digest"]),
                "require_authoritative_task": True,
            }
        result = self._track_derivation.derive_tracks(**arguments)
        return {
            **result,
            "planning_task_id": source_task_id,
            "assignment_id": assignment_id,
            "result_payload_digest": normalized["payload_digest"],
            "task_created": False,
            "queue_write": False,
        }

    @staticmethod
    def _request_binding(
        *,
        category: PlanningArtifactRevisionDB,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
        source_category_item_ids: Sequence[str],
        prompt_hash: str,
    ) -> dict[str, Any]:
        return {
            "schema": "organization_track_planning_binding.v1",
            "category_revision_id": category.id,
            "category_revision": category.revision,
            "category_digest": category.content_digest,
            "category_schema_hash": category.schema_hash,
            "policy_hash": category.policy_hash,
            "goal_id": category.goal_id,
            "organization_id": category.organization_id,
            "unit_id": unit_id,
            "team_id": team_id,
            "role_slot_id": role_slot_id,
            "source_category_item_ids": list(source_category_item_ids),
            "source_catalog_id": category.source_catalog_id,
            "source_catalog_hash": category.source_catalog_hash,
            "allowed_source_refs": list(category.allowed_source_refs or []),
            "allowed_run_refs": list(category.allowed_run_refs or []),
            "prompt_hash": prompt_hash,
            "prompt_template_ref": ("prompts/planning/organization_track_planning.j2"),
            "result_schema": TRACK_PLANNING_RESULT_SCHEMA,
            "result_payload_schema_ref": "todos/todo.track.schema.json",
            "result_digest_algorithm": "sha256-canonical-json-v1",
            "worker_authority_ceiling": {
                "allowed_task_capabilities": [],
                "allowed_tools": [],
                "allowed_context_refs": sorted(
                    set(category.allowed_source_refs or []) | set(category.allowed_run_refs or [])
                ),
                "worker_controls_routing": False,
                "worker_controls_budget": False,
            },
        }

    @staticmethod
    def _validate_category(
        category: PlanningArtifactRevisionDB | None,
        *,
        context: PlanningOperationContext,
        expected_category_digest: str,
        expected_policy_hash: str,
    ) -> None:
        if (
            category is None
            or category.artifact_type != "planning_category_todo"
            or category.tenant_id != context.tenant_id
            or category.project_id != context.project_id
            or category.organization_id != context.organization_id
        ):
            raise PlanningTransitionError("category_revision_not_found")
        if category.status != "promoted":
            raise PlanningTransitionError("category_revision_not_promoted")
        if category.content_digest != str(expected_category_digest or ""):
            raise PlanningTransitionError("category_digest_mismatch")
        if category.policy_hash != str(expected_policy_hash or ""):
            raise PlanningTransitionError("category_policy_hash_stale")

    @staticmethod
    def _validate_goal_and_binding(
        session: Session,
        *,
        context: PlanningOperationContext,
        goal_id: str,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
    ) -> None:
        goal = session.exec(
            select(GoalDB).where(
                GoalDB.id == goal_id,
                GoalDB.tenant_id == context.tenant_id,
                GoalDB.project_id == context.project_id,
                GoalDB.organization_id == context.organization_id,
            )
        ).one_or_none()
        team = session.exec(
            select(OrganizationTeamLinkDB).where(
                OrganizationTeamLinkDB.tenant_id == context.tenant_id,
                OrganizationTeamLinkDB.project_id == context.project_id,
                OrganizationTeamLinkDB.organization_id == context.organization_id,
                OrganizationTeamLinkDB.unit_id == unit_id,
                OrganizationTeamLinkDB.team_id == team_id,
                OrganizationTeamLinkDB.lifecycle.in_(("planned", "active")),
            )
        ).one_or_none()
        slot = session.exec(
            select(OrganizationRoleSlotDB).where(
                OrganizationRoleSlotDB.id == role_slot_id,
                OrganizationRoleSlotDB.tenant_id == context.tenant_id,
                OrganizationRoleSlotDB.project_id == context.project_id,
                OrganizationRoleSlotDB.organization_id == context.organization_id,
                OrganizationRoleSlotDB.unit_id == unit_id,
                OrganizationRoleSlotDB.lifecycle == "active",
            )
        ).one_or_none()
        if goal is None or str(goal.goal_kind or "") != "organization":
            raise PlanningTransitionError("organization_goal_not_found")
        if team is None or slot is None:
            raise PlanningTransitionError("track_planning_role_binding_invalid")

    @staticmethod
    def _validate_capability_binding(
        *,
        source_task_id: str,
        assignment_id: str,
        capability_claims: Mapping[str, Any],
    ) -> None:
        scopes = {str(value) for value in list(capability_claims.get("scopes") or [])}
        if (
            str(capability_claims.get("source_task_id") or "") != source_task_id
            or str(capability_claims.get("assignment_id") or "") != assignment_id
            or "worker.result.submit" not in scopes
            or not str(capability_claims.get("worker_id") or "")
            or not str(capability_claims.get("dispatch_lease_id") or "")
        ):
            raise PlanningTransitionError("track_planning_result_capability_invalid")

    @staticmethod
    def _normalize_source_ids(values: Sequence[str]) -> tuple[str, ...]:
        if not 1 <= len(values) <= 100:
            raise PlanningTransitionError("track_planning_category_scope_invalid")
        normalized = tuple(
            sorted(
                validate_track_planning_identifier(
                    value,
                    reason_code="track_planning_category_scope_invalid",
                )
                for value in values
            )
        )
        if len(set(normalized)) != len(normalized):
            raise PlanningTransitionError("track_planning_category_scope_invalid")
        return normalized

    @staticmethod
    def _authorize(context: PlanningOperationContext) -> None:
        if not context.hub_owned:
            raise PlanningTransitionError("planning_hub_authority_required")
        if "organization_admin" not in context.roles and "track_derive" not in context.allowed_operations:
            raise PlanningTransitionError("planning_organization_admin_required")

    @staticmethod
    def _require_idempotency_key(value: str) -> None:
        normalized = str(value or "").strip()
        if not 8 <= len(normalized) <= 191 or any(character.isspace() for character in normalized):
            raise PlanningTransitionError("planning_idempotency_key_required")

    @staticmethod
    def _task_response(task: TaskDB, *, replayed: bool) -> dict[str, Any]:
        binding = dict(task.worker_execution_context or {}).get("planning_track_binding")
        bound = dict(binding) if isinstance(binding, Mapping) else {}
        return {
            "task_id": task.id,
            "task_kind": task.task_kind,
            "status": task.status,
            "goal_id": task.goal_id,
            "organization_id": task.organization_id,
            "category_revision_id": bound.get("category_revision_id"),
            "category_revision": str(bound.get("category_revision") or ""),
            "category_digest": bound.get("category_digest"),
            "category_policy_hash": bound.get("policy_hash"),
            "source_category_item_ids": list(bound.get("source_category_item_ids") or []),
            "replayed": replayed,
            "materialized_task_ids": [],
        }

    @staticmethod
    def _stable_id(prefix: str, *values: str) -> str:
        digest = hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()[:24]
        return f"{prefix}-{digest}"

    @staticmethod
    def _digest(value: Any) -> str:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = ["OrganizationTrackPlanningService"]
