"""Scoped, cursor-paginated planning read model for one Organization.

Projects goals, planning artifact revisions and Worker task proposals of an
already authorized Organization into the node/proposal view used by the
Angular and TUI clients.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import and_, or_
from sqlmodel import Session, select

from agent.db_models import (
    GoalDB,
    OrganizationInstanceDB,
    PlanningArtifactRevisionDB,
    WorkerTaskProposalDB,
)
from agent.services.organization_membership_service import (
    OrganizationAccessPrincipal,
)
from agent.services.organization_planning_cursor_codec import PlanningCursorCodec
from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
)
from agent.services.planning_hierarchy_projection_service import (
    PlanningHierarchyProjectionService,
)


def hub_admin_operation_context(
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


class OrganizationPlanningReadModel:
    """Read the planning hierarchy of an authorized Organization."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        projection_service: PlanningHierarchyProjectionService,
        cursor_codec: PlanningCursorCodec,
    ) -> None:
        self._session_factory = session_factory
        self._projection = projection_service
        self._cursors = cursor_codec

    def read(
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

        context = hub_admin_operation_context(principal=principal, organization=organization)
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
            "nodes": planning_nodes(
                goals=goals,
                revisions=revisions,
                projections=projections,
            ),
            "proposals": [proposal_view(row) for row in proposals],
            "next_cursor": next_cursor,
        }


def planning_nodes(
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
                runtime_tracks[str(track.get("track_artifact_revision_id") or "")] = dict(track.get("payload") or {})

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


def proposal_view(proposal: WorkerTaskProposalDB) -> dict[str, Any]:
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


__all__ = [
    "OrganizationPlanningReadModel",
    "hub_admin_operation_context",
    "planning_nodes",
    "proposal_view",
]
