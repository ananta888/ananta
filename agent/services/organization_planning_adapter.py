from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlmodel import select

from agent.db_models import (
    GoalDB,
    OrganizationInstanceDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationUnitDB,
    PlanDB,
    PlanNodeDB,
    TeamDB,
)
from agent.services.planning_artifact_transition_service import (
    PlanningTransitionError,
)
from agent.services.planning_control_unit_of_work import PlanningControlUnitOfWork


@dataclass(frozen=True, slots=True)
class OrganizationPlanTaskBinding:
    execution_goal_id: str
    plan_id: str
    plan_node_id: str


@dataclass(frozen=True, slots=True)
class OrganizationPlanningStructure:
    organization_goal_id: str
    plan_id: str
    task_bindings: Mapping[str, OrganizationPlanTaskBinding]


class OrganizationPlanningAdapter:
    """Stage Organization Goal/Team Goal/PlanNode structure in a caller UoW.

    The adapter has no commit and no Task/queue port.  The guarded planning
    materializer calls it before staging runtime Tasks, so the whole aggregate
    either commits once or rolls back once.
    """

    def stage_structure(
        self,
        *,
        uow: PlanningControlUnitOfWork,
        track: Any,
        tasks: list[dict[str, Any]],
        internal_task_ids: Mapping[str, str],
    ) -> OrganizationPlanningStructure:
        if uow.session is None:
            raise RuntimeError("planning_uow_not_entered")
        session = uow.session
        organization = session.get(OrganizationInstanceDB, track.organization_id)
        if (
            organization is None
            or organization.tenant_id != track.tenant_id
            or organization.project_id != track.project_id
        ):
            raise PlanningTransitionError("planning_organization_not_found")

        root_goal = session.get(GoalDB, track.goal_id)
        if root_goal is None:
            root_goal = GoalDB(
                id=track.goal_id,
                trace_id=self._stable_id("goal", track.organization_id, track.goal_id),
                goal=str(
                    track.payload.get("goal")
                    or track.payload.get("purpose")
                    or track.payload.get("track")
                    or "Organization goal"
                ),
                summary=str(track.payload.get("purpose") or "") or None,
                status="planned",
                source="organization_planning",
                organization_id=track.organization_id,
                goal_kind="organization",
                tenant_id=track.tenant_id,
                project_id=track.project_id,
                execution_preferences={
                    "organization_id": track.organization_id,
                    "planning_track_revision_id": track.id,
                },
                mode_data={
                    "goal_kind": "organization",
                    "organization_id": track.organization_id,
                },
            )
            session.add(root_goal)
            session.flush()
        self._validate_root_goal(root_goal=root_goal, track=track)

        plan_id = self._stable_id("orgplan", track.id)
        plan = session.get(PlanDB, plan_id)
        if plan is None:
            plan = PlanDB(
                id=plan_id,
                goal_id=root_goal.id,
                trace_id=root_goal.trace_id,
                status="materialized",
                planning_mode="organization_track",
                rationale={
                    "schema": "organization_planning_structure.v1",
                    "organization_id": track.organization_id,
                    "category_revision_id": track.parent_revision_id,
                    "track_revision_id": track.id,
                    "track_digest": track.content_digest,
                },
            )
            session.add(plan)
            session.flush()
        elif plan.goal_id != root_goal.id:
            raise PlanningTransitionError("planning_plan_binding_conflict")

        task_bindings: dict[str, OrganizationPlanTaskBinding] = {}
        child_goal_by_team: dict[tuple[str, str], GoalDB] = {}
        node_id_by_task = {
            str(task.get("id") or ""): self._stable_id(
                "orgnode",
                track.id,
                str(task.get("id") or ""),
            )
            for task in tasks
        }
        for position, task in enumerate(tasks):
            plan_task_id = str(task.get("id") or "")
            binding = self._organization_binding(task)
            self._validate_topology_binding(
                uow=uow,
                track=track,
                unit_id=binding["unit_id"],
                team_id=binding["team_id"],
                role_slot_id=binding["role_slot_id"],
            )
            team_key = (binding["unit_id"], binding["team_id"])
            child_goal = child_goal_by_team.get(team_key)
            if child_goal is None:
                child_goal = self._stage_team_goal(
                    uow=uow,
                    track=track,
                    root_goal=root_goal,
                    unit_id=binding["unit_id"],
                    team_id=binding["team_id"],
                )
                child_goal_by_team[team_key] = child_goal

            node_id = node_id_by_task[plan_task_id]
            node_dependencies = [
                node_id_by_task[dependency]
                for raw_dependency in list(task.get("depends_on") or [])
                if (dependency := str(raw_dependency or "").split(":", 1)[-1]) in node_id_by_task
            ]
            node = session.get(PlanNodeDB, node_id)
            if node is None:
                node = PlanNodeDB(
                    id=node_id,
                    plan_id=plan_id,
                    tenant_id=track.tenant_id,
                    project_id=track.project_id,
                    organization_id=track.organization_id,
                    unit_id=binding["unit_id"],
                    team_id=binding["team_id"],
                    role_slot_id=binding["role_slot_id"],
                    node_key=plan_task_id,
                    title=str(task.get("title") or plan_task_id)[:300],
                    description=str(task.get("description") or "") or None,
                    priority=str(task.get("priority") or "Medium"),
                    status="materialized",
                    position=position,
                    depends_on=list(dict.fromkeys(node_dependencies)),
                    rationale={
                        "schema": "organization_plan_node_lineage.v1",
                        "organization_goal_id": root_goal.id,
                        "team_goal_id": child_goal.id,
                        "category_revision_id": track.parent_revision_id,
                        "track_revision_id": track.id,
                        "plan_task_id": plan_task_id,
                        "source_category_item_ids": list(task.get("source_category_item_ids") or []),
                        "task_kind": str(task.get("task_kind") or task.get("type") or "implementation"),
                        "required_capabilities": list(task.get("required_capabilities") or []),
                        "expected_outputs": list(task.get("expected_outputs") or []),
                        "gate": bool(task.get("gate", False)),
                    },
                    editable=False,
                    materialized_task_id=str(internal_task_ids[plan_task_id]),
                    verification_spec=(
                        dict(task.get("verification_spec") or {})
                        if isinstance(task.get("verification_spec"), Mapping)
                        else {}
                    ),
                )
                session.add(node)
                session.flush()
            self._validate_node(
                node=node,
                plan_id=plan_id,
                track=track,
                task=task,
                binding=binding,
                internal_task_id=str(internal_task_ids[plan_task_id]),
            )
            task_bindings[plan_task_id] = OrganizationPlanTaskBinding(
                execution_goal_id=child_goal.id,
                plan_id=plan_id,
                plan_node_id=node.id,
            )
        return OrganizationPlanningStructure(
            organization_goal_id=root_goal.id,
            plan_id=plan_id,
            task_bindings=task_bindings,
        )

    @staticmethod
    def _organization_binding(task: Mapping[str, Any]) -> dict[str, str]:
        nested = task.get("organization_binding")
        source = dict(nested) if isinstance(nested, Mapping) else {}
        result = {
            "unit_id": str(task.get("unit_id") or source.get("unit_id") or "").strip(),
            "team_id": str(task.get("team_id") or source.get("team_id") or "").strip(),
            "role_slot_id": str(task.get("role_slot_id") or source.get("role_slot_id") or "").strip(),
        }
        if any(not value for value in result.values()):
            raise PlanningTransitionError("planning_task_organization_binding_required")
        return result

    @staticmethod
    def _validate_root_goal(*, root_goal: GoalDB, track: Any) -> None:
        if (
            str(root_goal.organization_id or "") != track.organization_id
            or str(root_goal.tenant_id or "") != track.tenant_id
            or str(root_goal.project_id or "") != track.project_id
            or str(root_goal.goal_kind or "") != "organization"
        ):
            raise PlanningTransitionError("planning_organization_goal_binding_conflict")

    @staticmethod
    def _validate_topology_binding(
        *,
        uow: PlanningControlUnitOfWork,
        track: Any,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
    ) -> None:
        assert uow.session is not None
        session = uow.session
        unit = session.get(OrganizationUnitDB, unit_id)
        team = session.get(TeamDB, team_id)
        role_slot = session.get(OrganizationRoleSlotDB, role_slot_id)
        team_link = session.exec(
            select(OrganizationTeamLinkDB).where(
                OrganizationTeamLinkDB.tenant_id == track.tenant_id,
                OrganizationTeamLinkDB.project_id == track.project_id,
                OrganizationTeamLinkDB.organization_id == track.organization_id,
                OrganizationTeamLinkDB.unit_id == unit_id,
                OrganizationTeamLinkDB.team_id == team_id,
            )
        ).one_or_none()
        if (
            unit is None
            or unit.tenant_id != track.tenant_id
            or unit.project_id != track.project_id
            or unit.organization_id != track.organization_id
            or role_slot is None
            or role_slot.tenant_id != track.tenant_id
            or role_slot.project_id != track.project_id
            or role_slot.organization_id != track.organization_id
            or role_slot.unit_id != unit_id
            or team is None
            or team_link is None
        ):
            raise PlanningTransitionError("planning_task_topology_binding_invalid")

    def _stage_team_goal(
        self,
        *,
        uow: PlanningControlUnitOfWork,
        track: Any,
        root_goal: GoalDB,
        unit_id: str,
        team_id: str,
    ) -> GoalDB:
        assert uow.session is not None
        child_id = self._stable_id("teamgoal", root_goal.id, unit_id, team_id)
        child = uow.session.get(GoalDB, child_id)
        if child is None:
            child = GoalDB(
                id=child_id,
                trace_id=root_goal.trace_id,
                goal=f"{str(track.payload.get('track') or 'Organization plan')} / {team_id}",
                summary=f"Team-scoped execution goal derived from {root_goal.id}",
                status="planned",
                source="organization_planning",
                team_id=team_id,
                organization_id=track.organization_id,
                unit_id=unit_id,
                goal_kind="team",
                parent_goal_id=root_goal.id,
                tenant_id=track.tenant_id,
                project_id=track.project_id,
                execution_preferences={
                    "organization_id": track.organization_id,
                    "organization_goal_id": root_goal.id,
                    "planning_track_revision_id": track.id,
                },
                mode_data={
                    "goal_kind": "team",
                    "organization_id": track.organization_id,
                    "unit_id": unit_id,
                    "team_id": team_id,
                },
            )
            uow.session.add(child)
            uow.session.flush()
        if (
            child.parent_goal_id != root_goal.id
            or child.tenant_id != track.tenant_id
            or child.project_id != track.project_id
            or child.organization_id != track.organization_id
            or child.unit_id != unit_id
            or child.team_id != team_id
        ):
            raise PlanningTransitionError("planning_team_goal_binding_conflict")
        return child

    @staticmethod
    def _validate_node(
        *,
        node: PlanNodeDB,
        plan_id: str,
        track: Any,
        task: Mapping[str, Any],
        binding: Mapping[str, str],
        internal_task_id: str,
    ) -> None:
        if (
            node.plan_id != plan_id
            or node.tenant_id != track.tenant_id
            or node.project_id != track.project_id
            or node.organization_id != track.organization_id
            or node.unit_id != binding["unit_id"]
            or node.team_id != binding["team_id"]
            or node.role_slot_id != binding["role_slot_id"]
            or node.node_key != str(task.get("id") or "")
            or node.materialized_task_id != internal_task_id
        ):
            raise PlanningTransitionError("planning_plan_node_binding_conflict")

    @staticmethod
    def _stable_id(prefix: str, *values: str) -> str:
        digest = hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()[:24]
        return f"{prefix}-{digest}"


def organization_id_from_goal(goal: Any | None) -> str:
    if goal is None:
        return ""
    if isinstance(goal, Mapping):
        payload = dict(goal)
    elif hasattr(goal, "model_dump"):
        payload = dict(goal.model_dump())
    else:
        payload = {
            "organization_id": getattr(goal, "organization_id", None),
            "goal_kind": getattr(goal, "goal_kind", None),
            "mode_data": getattr(goal, "mode_data", None),
            "execution_preferences": getattr(goal, "execution_preferences", None),
        }
    organization_id = str(payload.get("organization_id") or "").strip()
    mode_data = payload.get("mode_data") if isinstance(payload.get("mode_data"), Mapping) else {}
    preferences = (
        payload.get("execution_preferences") if isinstance(payload.get("execution_preferences"), Mapping) else {}
    )
    return str(organization_id or mode_data.get("organization_id") or preferences.get("organization_id") or "").strip()


def is_organization_goal(goal: Any | None) -> bool:
    if goal is None:
        return False
    if organization_id_from_goal(goal):
        return True
    if isinstance(goal, Mapping):
        goal_kind = goal.get("goal_kind")
        mode_data = goal.get("mode_data")
    else:
        goal_kind = getattr(goal, "goal_kind", None)
        mode_data = getattr(goal, "mode_data", None)
    if str(goal_kind or "").strip().lower() == "organization":
        return True
    return isinstance(mode_data, Mapping) and str(mode_data.get("goal_kind") or "").strip().lower() == "organization"


def organization_id_from_task(task: Any | None) -> str:
    if task is None:
        return ""
    payload = (
        dict(task)
        if isinstance(task, Mapping)
        else dict(task.model_dump())
        if hasattr(task, "model_dump")
        else {"organization_id": getattr(task, "organization_id", None)}
    )
    direct = str(payload.get("organization_id") or "").strip()
    if direct:
        return direct
    context = payload.get("worker_execution_context")
    if not isinstance(context, Mapping):
        return ""
    lineage = context.get("planning_lineage")
    if isinstance(lineage, Mapping):
        return str(lineage.get("organization_id") or "").strip()
    return ""


def load_goal_for_planning(goal_id: str | None) -> Any | None:
    normalized = str(goal_id or "").strip()
    if not normalized:
        return None
    from agent.services.repository_registry import get_repository_registry

    return get_repository_registry().goal_repo.get_by_id(normalized)


def organization_planning_required_response(*, goal_id: str | None, organization_id: str) -> dict[str, Any]:
    return {
        "subtasks": [],
        "created_task_ids": [],
        "error": "organization_category_planning_required",
        "error_classification": "organization_planning_legacy_bypass_blocked",
        "goal_id": str(goal_id or "") or None,
        "organization_id": organization_id,
        "planning_stage": "research_category_todo",
        "artifact_type": "planning_category_todo",
        "schema_ref": "todos/todo.schema.json",
        "next_transition": "category_promotion",
    }


__all__ = [
    "OrganizationPlanningAdapter",
    "OrganizationPlanningStructure",
    "OrganizationPlanTaskBinding",
    "is_organization_goal",
    "load_goal_for_planning",
    "organization_id_from_goal",
    "organization_id_from_task",
    "organization_planning_required_response",
]
