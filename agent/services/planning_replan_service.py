from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent.db_models import PlanningArtifactRevisionDB, PlanningLineageDB, TaskDB
from agent.services.task_dependency_policy import validate_dependency_graph
from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.planning_category_contract_service import stable_planning_digest
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
    planning_scope_lock,
)
from agent.services.planning_principal_identity_service import (
    canonical_planning_actor_id,
)
from agent.services.planning_summary_engine import PlanningSummaryEngine
from agent.services.planning_track_contract_service import planning_contract_hash
from agent.services.planning_track_pipeline_service import (
    evaluate_planning_quality_gates,
    validate_planning_track_with_details,
)


class PlanningReplanService:
    """Create an immutable replacement Track; never create Tasks or budgets."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
    ) -> None:
        self._uow_factory = uow_factory or PlanningControlUnitOfWork

    def create_track_revision(
        self,
        *,
        context: PlanningOperationContext,
        source_track_revision_id: str,
        expected_track_digest: str,
        expected_policy_hash: str,
        replacement_payload: Mapping[str, Any],
        replaced_plan_task_ids: Sequence[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._authorize(context)
        if not str(idempotency_key or "").strip():
            raise PlanningTransitionError("planning_idempotency_key_required")
        scope_key = f"planning-replan:{source_track_revision_id}"
        with planning_scope_lock(scope_key), self._uow_factory() as uow:
            assert uow.planning is not None and uow.session is not None
            uow.planning.acquire_scope_lock(scope_key)
            source = uow.planning.get_revision(
                source_track_revision_id,
                for_update=True,
            )
            if source is None or source.artifact_type != "planning_track":
                raise PlanningTransitionError("planning_track_revision_not_found")
            self._validate_scope(context=context, row=source)
            if source.status != "adopted":
                raise PlanningTransitionError("planning_track_not_adopted")
            if source.content_digest != str(expected_track_digest or ""):
                raise PlanningTransitionError("planning_revision_digest_mismatch")
            if source.policy_hash != str(expected_policy_hash or ""):
                raise PlanningTransitionError("planning_policy_hash_stale")
            if stable_planning_digest(source.payload) != source.content_digest:
                raise PlanningTransitionError("planning_track_payload_digest_stale")
            category = uow.planning.get_revision(
                str(source.parent_revision_id or ""),
                for_update=True,
            )
            if category is None or category.status != "promoted":
                raise PlanningTransitionError("planning_category_not_promoted")

            replay = next(
                (
                    row
                    for row in uow.planning.list_revisions(
                        goal_id=source.goal_id,
                        organization_id=source.organization_id,
                        artifact_type="planning_track",
                    )
                    if str(dict(row.execution_provenance or {}).get("replan_idempotency_key") or "")
                    == str(idempotency_key)
                ),
                None,
            )
            if replay is not None:
                if replay.supersedes_revision_id != source.id or dict(replay.execution_provenance or {}).get(
                    "replan_request_digest"
                ) != self._request_digest(
                    replacement_payload=replacement_payload,
                    replaced_plan_task_ids=replaced_plan_task_ids,
                ):
                    raise PlanningTransitionError("planning_replan_idempotency_conflict")
                return self._response(replay, replayed=True)

            payload, retained_ids, replaced_ids, lineage = self._prepare_payload(
                uow=uow,
                source=source,
                replacement_payload=replacement_payload,
                replaced_plan_task_ids=replaced_plan_task_ids,
            )
            request_digest = self._request_digest(
                replacement_payload=replacement_payload,
                replaced_plan_task_ids=replaced_plan_task_ids,
            )
            payload["planning_replan"] = {
                "schema": "planning_replan.v1",
                "source_track_artifact_revision_id": source.id,
                "source_track_revision": source.revision,
                "source_track_digest": source.content_digest,
                "retained_plan_task_ids": sorted(retained_ids),
                "replaced_plan_task_ids": sorted(replaced_ids),
                "runtime_tasks_reinterpreted": False,
                "budget_reservation_created": False,
            }
            payload, summary_issues = PlanningSummaryEngine().recompute(payload)
            schema_issues = validate_planning_track_with_details(payload)
            quality = evaluate_planning_quality_gates(
                payload,
                large_goal_mode=bool(payload.get("large_goal_mode")),
                small_goal_mode=bool(payload.get("small_goal_mode")),
            )
            if schema_issues or summary_issues or not bool(quality.get("ok")):
                raise PlanningTransitionError("planning_replan_track_invalid")

            digest = stable_planning_digest(payload)
            revision_number = uow.planning.next_revision_number(artifact_id=source.artifact_id)
            revision = PlanningArtifactRevisionDB(
                id=self._revision_id(
                    artifact_id=source.artifact_id,
                    revision=revision_number,
                    digest=digest,
                ),
                artifact_id=source.artifact_id,
                revision=revision_number,
                artifact_type="planning_track",
                tenant_id=source.tenant_id,
                project_id=source.project_id,
                organization_id=source.organization_id,
                goal_id=source.goal_id,
                status="valid",
                payload=payload,
                content_digest=digest,
                schema_ref="todos/todo.track.schema.json",
                schema_hash=planning_contract_hash(),
                prompt_hash="",
                policy_hash=source.policy_hash,
                source_catalog_id=source.source_catalog_id,
                source_catalog_hash=source.source_catalog_hash,
                allowed_source_refs=list(source.allowed_source_refs or []),
                allowed_run_refs=list(source.allowed_run_refs or []),
                source_category_item_ids=list(source.source_category_item_ids or []),
                execution_provenance={
                    "schema": "planning_replan_provenance.v1",
                    "source_category_digest": category.content_digest,
                    "replan_source_track_revision_id": source.id,
                    "replan_source_track_digest": source.content_digest,
                    "replan_idempotency_key": str(idempotency_key),
                    "replan_request_digest": request_digest,
                    "created_by_hub_actor": context.subject_id,
                },
                validation_result={
                    "valid": True,
                    "summary_recalculation_status": "recalculated",
                    "quality_gate_warnings": list(quality.get("warnings") or []),
                },
                parent_revision_id=category.id,
                supersedes_revision_id=source.id,
                created_by=f"hub:replan:{context.subject_id}",
                created_by_principal_id=canonical_planning_actor_id(context.subject_id),
            )
            uow.planning.add_revision(revision)
            uow.planning.add_lineage(
                [
                    PlanningLineageDB(
                        tenant_id=row.tenant_id,
                        project_id=row.project_id,
                        organization_id=row.organization_id,
                        goal_id=row.goal_id,
                        category_revision_id=row.category_revision_id,
                        track_revision_id=revision.id,
                        source_category_item_id=row.source_category_item_id,
                        plan_task_id=row.plan_task_id,
                    )
                    for row in lineage
                ]
            )
        return self._response(revision, replayed=False)

    @staticmethod
    def _prepare_payload(
        *,
        uow: PlanningControlUnitOfWork,
        source: PlanningArtifactRevisionDB,
        replacement_payload: Mapping[str, Any],
        replaced_plan_task_ids: Sequence[str],
    ) -> tuple[dict[str, Any], set[str], set[str], list[PlanningLineageDB]]:
        assert uow.planning is not None and uow.session is not None
        payload = copy.deepcopy(dict(replacement_payload or {}))
        payload["source_category_item_ids"] = list(source.source_category_item_ids or [])
        source_tasks = {
            str(row.get("id") or ""): dict(row)
            for row in list(source.payload.get("tasks") or [])
            if isinstance(row, Mapping) and str(row.get("id") or "")
        }
        candidate_tasks = {
            str(row.get("id") or ""): dict(row)
            for row in list(payload.get("tasks") or [])
            if isinstance(row, Mapping) and str(row.get("id") or "")
        }
        if len(candidate_tasks) != len(list(payload.get("tasks") or [])):
            raise PlanningTransitionError("planning_replan_task_id_duplicate")
        source_mappings = {row.plan_task_id: row for row in uow.planning.list_mappings(source.id)}
        completed_ids = {
            plan_task_id
            for plan_task_id, mapping in source_mappings.items()
            if (
                (task := uow.session.get(TaskDB, mapping.internal_task_id)) is not None
                and str(task.status or "") == "completed"
            )
        }
        for plan_task_id in completed_ids:
            preserved = copy.deepcopy(source_tasks[plan_task_id])
            preserved["status"] = "done"
            preserved["replan_disposition"] = "preserved_completed"
            candidate_tasks[plan_task_id] = preserved

        retained_ids = set(source_tasks) & set(candidate_tasks)
        for plan_task_id in retained_ids - completed_ids:
            if PlanningReplanService._task_identity(source_tasks[plan_task_id]) != PlanningReplanService._task_identity(
                candidate_tasks[plan_task_id]
            ):
                raise PlanningTransitionError("planning_replan_retained_task_changed")
            candidate_tasks[plan_task_id]["replan_disposition"] = "retained"

        replaced_ids = {str(value or "").strip() for value in replaced_plan_task_ids if str(value or "").strip()}
        expected_replaced = set(source_tasks) - retained_ids
        if replaced_ids != expected_replaced:
            raise PlanningTransitionError("planning_replan_replaced_tasks_not_declared")
        if replaced_ids & completed_ids:
            raise PlanningTransitionError("planning_replan_completed_task_replaced")

        new_ids = set(candidate_tasks) - set(source_tasks)
        for plan_task_id in new_ids:
            if uow.planning.find_mappings_for_plan_task(
                goal_id=source.goal_id,
                plan_task_id=plan_task_id,
            ):
                raise PlanningTransitionError("planning_replan_plan_task_id_reused")
            source_items = {
                str(value)
                for value in list(candidate_tasks[plan_task_id].get("source_category_item_ids") or [])
                if str(value)
            }
            if not source_items or not source_items.issubset(set(source.source_category_item_ids or [])):
                raise PlanningTransitionError("planning_replan_category_scope_expansion")
            candidate_tasks[plan_task_id]["replan_disposition"] = "new"

        graph = {
            task_id: [str(value).split(":", 1)[-1] for value in list(task.get("depends_on") or []) if str(value)]
            for task_id, task in candidate_tasks.items()
        }
        if any(dependency not in graph for dependencies in graph.values() for dependency in dependencies):
            raise PlanningTransitionError("planning_replan_dependency_unknown")
        valid, reason = validate_dependency_graph(graph)
        if not valid:
            raise PlanningTransitionError(reason or "planning_replan_dependency_invalid")
        payload["tasks"] = list(candidate_tasks.values())

        source_lineage = uow.planning.list_lineage_for_track(source.id)
        lineage = [row for row in source_lineage if row.plan_task_id in retained_ids]
        lineage.extend(
            PlanningLineageDB(
                tenant_id=source.tenant_id,
                project_id=source.project_id,
                organization_id=source.organization_id,
                goal_id=source.goal_id,
                category_revision_id=str(source.parent_revision_id or ""),
                track_revision_id="pending",
                source_category_item_id=source_item_id,
                plan_task_id=plan_task_id,
            )
            for plan_task_id in new_ids
            for source_item_id in list(candidate_tasks[plan_task_id].get("source_category_item_ids") or [])
        )
        if {row.plan_task_id for row in lineage} != set(candidate_tasks):
            raise PlanningTransitionError("planning_replan_lineage_incomplete")
        return payload, retained_ids, replaced_ids, lineage

    @staticmethod
    def _task_identity(task: Mapping[str, Any]) -> str:
        ignored = {"status", "progress_percent", "replan_disposition"}
        return stable_planning_digest({key: value for key, value in dict(task).items() if key not in ignored})

    @staticmethod
    def _request_digest(
        *,
        replacement_payload: Mapping[str, Any],
        replaced_plan_task_ids: Sequence[str],
    ) -> str:
        return stable_planning_digest(
            {
                "replacement_payload": dict(replacement_payload or {}),
                "replaced_plan_task_ids": sorted(str(value) for value in replaced_plan_task_ids),
            }
        )

    @staticmethod
    def _authorize(context: PlanningOperationContext) -> None:
        if not context.hub_owned:
            raise PlanningTransitionError("planning_hub_authority_required")
        if "organization_admin" not in context.roles and "track_replan" not in context.allowed_operations:
            raise PlanningTransitionError("planning_replan_authority_required")

    @staticmethod
    def _validate_scope(*, context: PlanningOperationContext, row: Any) -> None:
        if (
            row.tenant_id != context.tenant_id
            or row.project_id != context.project_id
            or row.organization_id != context.organization_id
        ):
            raise PlanningTransitionError("planning_scope_forbidden")

    @staticmethod
    def _revision_id(*, artifact_id: str, revision: int, digest: str) -> str:
        value = f"{artifact_id}:{revision}:{digest}".encode("utf-8")
        return f"ptrk-{hashlib.sha256(value).hexdigest()[:24]}"

    @staticmethod
    def _response(
        revision: PlanningArtifactRevisionDB,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        metadata = dict(revision.payload.get("planning_replan") or {})
        return {
            "track_artifact_revision_id": revision.id,
            "track_revision": revision.revision,
            "track_digest": revision.content_digest,
            "source_track_artifact_revision_id": metadata.get("source_track_artifact_revision_id"),
            "source_track_revision": metadata.get("source_track_revision"),
            "source_track_digest": metadata.get("source_track_digest"),
            "retained_plan_task_ids": list(metadata.get("retained_plan_task_ids") or []),
            "replaced_plan_task_ids": list(metadata.get("replaced_plan_task_ids") or []),
            "status": revision.status,
            "replayed": replayed,
            "task_created": False,
            "queue_write": False,
            "budget_reservation_created": False,
        }


__all__ = ["PlanningReplanService"]
