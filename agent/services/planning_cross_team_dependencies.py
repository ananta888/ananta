"""Dependency resolution and cross-team dependency staging for materialized planning tasks."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from agent.db_models import (
    CrossTeamTaskDependencyDB,
    PlanningTaskMappingDB,
    TaskDB,
)
from agent.services.planning_artifact_transition_service import (
    PlanningTransitionError,
)
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
)
from agent.services.planning_materialization_ids import stable_planning_id


def resolve_task_dependencies(
    *,
    uow: PlanningControlUnitOfWork,
    track: Any,
    task: Mapping[str, Any],
    local_mappings: Mapping[str, PlanningTaskMappingDB],
) -> list[str]:
    assert uow.planning is not None
    resolved: list[str] = []
    for raw_ref in list(task.get("depends_on") or []):
        ref = str(raw_ref or "").strip()
        local_id = ref.split(":", 1)[-1]
        local = local_mappings.get(local_id)
        if local is not None:
            resolved.append(local.internal_task_id)
            continue
        matches = uow.planning.find_mappings_for_plan_task(goal_id=track.goal_id, plan_task_id=local_id)
        if len(matches) != 1:
            raise PlanningTransitionError("planning_dependency_mapping_unresolved")
        resolved.append(matches[0].internal_task_id)
    return list(dict.fromkeys(resolved))


def stage_cross_team_dependencies(
    *,
    uow: PlanningControlUnitOfWork,
    track: Any,
    tasks: list[dict[str, Any]],
    mappings: Mapping[str, PlanningTaskMappingDB],
) -> None:
    """Persist the cross-team subset of the authoritative Task DAG."""

    assert uow.session is not None and uow.planning is not None
    task_by_plan_id = {str(task.get("id") or ""): task for task in tasks if str(task.get("id") or "")}
    for target_plan_id, target_task in task_by_plan_id.items():
        target_mapping = mappings[target_plan_id]
        dependency_ids = resolve_task_dependencies(
            uow=uow,
            track=track,
            task=target_task,
            local_mappings=mappings,
        )
        for source_task_id in dependency_ids:
            source_task = uow.session.get(TaskDB, source_task_id)
            if source_task is None:
                raise PlanningTransitionError("planning_dependency_task_missing")
            if (
                str(source_task.tenant_id or "") != track.tenant_id
                or str(source_task.project_id or "") != track.project_id
                or str(source_task.organization_id or "") != track.organization_id
            ):
                raise PlanningTransitionError("planning_dependency_scope_mismatch")
            source_team_id = str(source_task.team_id or "")
            if not source_team_id:
                raise PlanningTransitionError("planning_dependency_team_binding_missing")
            if source_team_id == target_mapping.team_id:
                continue
            dependency_id = stable_planning_id(
                "xdep",
                track.organization_id,
                source_task_id,
                target_mapping.internal_task_id,
            )
            expected = CrossTeamTaskDependencyDB(
                id=dependency_id,
                tenant_id=track.tenant_id,
                project_id=track.project_id,
                organization_id=track.organization_id,
                source_task_id=source_task_id,
                target_task_id=target_mapping.internal_task_id,
                source_team_id=source_team_id,
                target_team_id=target_mapping.team_id,
                owner_ref=target_mapping.role_slot_id,
                gate_ref=(str(target_task.get("gate_ref") or "").strip() or None),
                required_artifact_refs=sorted(
                    {
                        str(value).strip()
                        for value in list(target_task.get("required_artifact_refs") or [])
                        if str(value).strip()
                    }
                ),
                due_at=optional_due_at(target_task.get("due_at") or target_task.get("due_date")),
                status="pending",
                blocking_reason="awaiting_source_task",
                escalation_policy=(str(target_task.get("escalation_policy") or "hub").strip() or "hub"),
            )
            existing = uow.session.get(CrossTeamTaskDependencyDB, dependency_id)
            if existing is None:
                uow.session.add(expected)
                continue
            bindings = (
                "tenant_id",
                "project_id",
                "organization_id",
                "source_task_id",
                "target_task_id",
                "source_team_id",
                "target_team_id",
                "owner_ref",
            )
            if any(getattr(existing, key) != getattr(expected, key) for key in bindings):
                raise PlanningTransitionError("planning_cross_team_dependency_binding_conflict")


def optional_due_at(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise PlanningTransitionError("planning_dependency_due_at_invalid")
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError) as exc:
        raise PlanningTransitionError("planning_dependency_due_at_invalid") from exc


__all__ = [
    "optional_due_at",
    "resolve_task_dependencies",
    "stage_cross_team_dependencies",
]
