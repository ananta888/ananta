from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from agent.db_models import TaskDB
from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.planning_category_contract_service import stable_planning_digest
from agent.services.planning_control_unit_of_work import PlanningControlUnitOfWork
from agent.services.planning_summary_engine import PlanningSummaryEngine


class PlanningStatusProjectionService:
    """Read-only Task -> Track -> Category projection from normalized truth."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
    ) -> None:
        self._uow_factory = uow_factory or PlanningControlUnitOfWork

    def project_goal(
        self,
        *,
        context: PlanningOperationContext,
        goal_id: str,
    ) -> dict[str, Any]:
        if not context.hub_owned:
            raise PlanningTransitionError("planning_hub_authority_required")
        with self._uow_factory() as uow:
            assert uow.planning is not None and uow.session is not None
            revisions = uow.planning.list_revisions(
                goal_id=goal_id,
                organization_id=context.organization_id,
            )
            if any(row.tenant_id != context.tenant_id or row.project_id != context.project_id for row in revisions):
                raise PlanningTransitionError("planning_scope_forbidden")
            categories = [row for row in revisions if row.artifact_type == "planning_category_todo"]
            tracks = [row for row in revisions if row.artifact_type == "planning_track"]
            revision_by_id = {row.id: row for row in revisions}
            active_category = next((row for row in categories if row.status == "promoted"), None)
            active_tracks = [row for row in tracks if row.status == "adopted"]
            candidate_tracks = [row for row in tracks if row.status == "valid"]
            proposals = uow.planning.list_proposals(
                organization_id=context.organization_id,
                source_goal_id=goal_id,
            )
            amendment_inputs = uow.planning.list_amendment_inputs(
                organization_id=context.organization_id,
                goal_id=goal_id,
            )
            if any(
                row.tenant_id != context.tenant_id
                or row.project_id != context.project_id
                or row.organization_id != context.organization_id
                for row in proposals
            ):
                raise PlanningTransitionError("planning_scope_forbidden")
            if any(
                row.tenant_id != context.tenant_id
                or row.project_id != context.project_id
                or row.organization_id != context.organization_id
                for row in amendment_inputs
            ):
                raise PlanningTransitionError("planning_scope_forbidden")

            track_projections: list[dict[str, Any]] = []
            status_by_category_item: dict[str, list[str]] = {}
            for track in active_tracks:
                source_category = revision_by_id.get(str(track.parent_revision_id or ""))
                mappings = uow.planning.list_mappings(track.id)
                task_by_plan_id: dict[str, TaskDB] = {}
                for mapping in mappings:
                    task = uow.session.get(TaskDB, mapping.internal_task_id)
                    if task is not None:
                        task_by_plan_id[mapping.plan_task_id] = task
                        for category_item_id in list(mapping.source_category_item_ids or []):
                            status_by_category_item.setdefault(category_item_id, []).append(
                                self._plan_status(task.status)
                            )
                payload = {
                    **dict(track.payload or {}),
                    "tasks": [
                        {
                            **dict(plan_task),
                            "status": self._plan_status(task_by_plan_id[str(plan_task.get("id") or "")].status)
                            if str(plan_task.get("id") or "") in task_by_plan_id
                            else str(plan_task.get("status") or "todo"),
                        }
                        for plan_task in list(track.payload.get("tasks") or [])
                        if isinstance(plan_task, Mapping)
                    ],
                }
                payload, _issues = PlanningSummaryEngine().recompute(payload)
                track_projections.append(
                    {
                        "track_artifact_revision_id": track.id,
                        "artifact_id": track.artifact_id,
                        "track_revision": track.revision,
                        "track_digest": track.content_digest,
                        "source_category_artifact_revision_id": track.parent_revision_id,
                        "source_category_revision": (source_category.revision if source_category is not None else None),
                        "source_category_digest": str(
                            dict(track.execution_provenance or {}).get("source_category_digest") or ""
                        ),
                        "payload": payload,
                        "materialization_status": "materialized" if mappings else "not_materialized",
                        "drift": stable_planning_digest(track.payload) != track.content_digest,
                        "blockers": sorted(
                            str(plan_task.get("id") or "")
                            for plan_task in list(payload.get("tasks") or [])
                            if isinstance(plan_task, Mapping) and str(plan_task.get("status") or "") == "blocked"
                        ),
                    }
                )

            category_projection = None
            if active_category is not None:
                payload = {
                    **dict(active_category.payload or {}),
                    "categories": [
                        {
                            **dict(group),
                            "items": [
                                {
                                    **dict(item),
                                    "status": self._aggregate_status(
                                        status_by_category_item.get(str(item.get("id") or ""), [])
                                    ),
                                }
                                for item in list(group.get("items") or [])
                                if isinstance(item, Mapping)
                            ],
                        }
                        for group in list(active_category.payload.get("categories") or [])
                        if isinstance(group, Mapping)
                    ],
                }
                items = [item for group in payload["categories"] for item in list(group.get("items") or [])]
                statuses = Counter(str(item.get("status") or "open") for item in items)
                for key in ("completed", "partial", "open"):
                    statuses.setdefault(key, 0)
                payload["meta"] = {
                    **dict(payload.get("meta") or {}),
                    "total_items": len(items),
                    "by_status": dict(statuses),
                }
                category_projection = {
                    "category_artifact_revision_id": active_category.id,
                    "category_revision": active_category.revision,
                    "category_digest": active_category.content_digest,
                    "payload": payload,
                    "drift": stable_planning_digest(active_category.payload) != active_category.content_digest,
                }

            task_statuses = [
                str(task.status)
                for projection in track_projections
                for mapping in uow.planning.list_mappings(projection["track_artifact_revision_id"])
                if (task := uow.session.get(TaskDB, mapping.internal_task_id)) is not None
            ]
            proposal_projections: list[dict[str, Any]] = []
            for row in proposals:
                amendment = (
                    uow.planning.get_revision(row.amendment_track_revision_id)
                    if row.amendment_track_revision_id
                    else None
                )
                proposal_projections.append(
                    {
                        "proposal_id": row.proposal_id,
                        "proposal_revision": row.proposal_revision,
                        "proposal_digest": row.envelope_digest,
                        "payload_digest": row.payload_digest,
                        "state": row.state,
                        "reason_code": row.reason_code,
                        "approval_request_id": row.approval_request_id,
                        "category_artifact_revision_id": dict(row.decision or {}).get("category_artifact_revision_id"),
                        "category_revision": dict(row.decision or {}).get("category_revision"),
                        "category_digest": dict(row.decision or {}).get("category_digest"),
                        "source_track_artifact_revision_id": dict(row.decision or {}).get(
                            "source_track_artifact_revision_id"
                        ),
                        "source_track_revision": dict(row.decision or {}).get("source_track_revision"),
                        "source_track_digest": dict(row.decision or {}).get("source_track_digest"),
                        "amendment_track_artifact_revision_id": (amendment.id if amendment is not None else None),
                        "amendment_track_revision": (amendment.revision if amendment is not None else None),
                        "amendment_track_digest": (amendment.content_digest if amendment is not None else None),
                    }
                )
            return {
                "goal_id": goal_id,
                "organization_id": context.organization_id,
                "category": category_projection,
                "tracks": track_projections,
                "track_candidates": [
                    {
                        "track_artifact_revision_id": row.id,
                        "track_revision": row.revision,
                        "track_digest": row.content_digest,
                        "source_category_artifact_revision_id": row.parent_revision_id,
                        "source_category_revision": (
                            revision_by_id[row.parent_revision_id].revision
                            if row.parent_revision_id in revision_by_id
                            else None
                        ),
                        "source_category_digest": str(
                            dict(row.execution_provenance or {}).get("source_category_digest") or ""
                        ),
                        "supersedes_track_artifact_revision_id": row.supersedes_revision_id,
                        "candidate_kind": (
                            "replan"
                            if dict(row.payload or {}).get("planning_replan")
                            else "amendment"
                            if str(row.created_by or "").startswith("hub:proposal:")
                            else "derived"
                        ),
                    }
                    for row in candidate_tracks
                ],
                "proposals": proposal_projections,
                "amendment_inputs": [
                    {
                        "amendment_input_id": row.id,
                        "amendment_input_revision": row.revision,
                        "amendment_input_digest": row.content_digest,
                        "input_kind": row.input_kind,
                        "source_task_id": row.source_task_id,
                        "state": row.state,
                    }
                    for row in amendment_inputs
                ],
                "organization_goal_status": self._aggregate_goal_status(task_statuses),
            }

    @staticmethod
    def _plan_status(runtime_status: str) -> str:
        return {
            "completed": "done",
            "in_progress": "in_progress",
            "assigned": "in_progress",
            "delegated": "in_progress",
            "failed": "blocked",
            "cancelled": "blocked",
            "verification_failed": "blocked",
            "blocked": "blocked",
            "blocked_by_dependency": "blocked",
        }.get(str(runtime_status or ""), "todo")

    @staticmethod
    def _aggregate_status(statuses: list[str]) -> str:
        if statuses and all(status == "done" for status in statuses):
            return "completed"
        if any(status == "blocked" for status in statuses):
            return "partial"
        if any(status in {"done", "in_progress"} for status in statuses):
            return "partial"
        return "open"

    @staticmethod
    def _aggregate_goal_status(statuses: list[str]) -> str:
        if statuses and all(status == "completed" for status in statuses):
            return "completed"
        if any(
            status
            in {
                "failed",
                "cancelled",
                "verification_failed",
                "blocked",
                "blocked_by_dependency",
            }
            for status in statuses
        ):
            return "blocked"
        if any(status in {"assigned", "delegated", "in_progress"} for status in statuses):
            return "in_progress"
        return "planned" if statuses else "planning"


__all__ = ["PlanningStatusProjectionService"]
