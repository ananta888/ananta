"""Authoritative cross-team task dependency DAG rules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class OrganizationTaskRef:
    task_id: str
    team_id: str
    status: str
    replacement_task_id: str | None = None


@dataclass(frozen=True, slots=True)
class OrganizationTaskDependency:
    dependency_id: str
    organization_id: str
    source_task_id: str
    source_team_id: str
    target_task_id: str
    target_team_id: str
    owner_role_slot_id: str
    gate_id: str | None
    required_artifact_refs: tuple[str, ...]
    due_at: datetime | None
    status: str
    blocking_reason: str | None
    escalation_rule: str
    namespace: str = "runtime"
    kind: str = "runtime_task_dependency"


@dataclass(frozen=True, slots=True)
class DependencyValidationResult:
    valid: bool
    reason_codes: tuple[str, ...]
    topological_task_ids: tuple[str, ...]


class OrganizationDependencyService:
    """Validates and projects dependencies without owning task persistence."""

    TERMINAL_SUCCESS = frozenset({"done", "completed", "verified"})
    TERMINAL_FAILURE = frozenset({"failed", "cancelled", "archived"})

    def validate(
        self,
        *,
        tasks: Iterable[OrganizationTaskRef],
        dependencies: Iterable[OrganizationTaskDependency],
    ) -> DependencyValidationResult:
        task_rows = tuple(tasks)
        dependency_rows = tuple(dependencies)
        tasks_by_id = {row.task_id: row for row in task_rows if row.task_id}
        issues: list[str] = []
        if len(tasks_by_id) != len(task_rows):
            issues.append("task_identity_duplicate_or_missing")
        seen_dependencies: set[str] = set()
        adjacency: dict[str, set[str]] = {task_id: set() for task_id in tasks_by_id}
        indegree = {task_id: 0 for task_id in tasks_by_id}
        for dependency in dependency_rows:
            if dependency.dependency_id in seen_dependencies or not dependency.dependency_id:
                issues.append("dependency_identity_duplicate_or_missing")
            seen_dependencies.add(dependency.dependency_id)
            source = tasks_by_id.get(dependency.source_task_id)
            target = tasks_by_id.get(dependency.target_task_id)
            if source is None or target is None:
                issues.append(f"dependency_orphan:{dependency.dependency_id}")
                continue
            if source.team_id != dependency.source_team_id or target.team_id != dependency.target_team_id:
                issues.append(f"dependency_team_binding_mismatch:{dependency.dependency_id}")
            if source.task_id == target.task_id:
                issues.append(f"dependency_self_cycle:{dependency.dependency_id}")
                continue
            if target.task_id not in adjacency[source.task_id]:
                adjacency[source.task_id].add(target.task_id)
                indegree[target.task_id] += 1
            if not dependency.owner_role_slot_id:
                issues.append(f"dependency_owner_missing:{dependency.dependency_id}")
            if not dependency.escalation_rule:
                issues.append(f"dependency_escalation_missing:{dependency.dependency_id}")
            if dependency.namespace != "runtime" or dependency.kind != "runtime_task_dependency":
                issues.append(f"dependency_namespace_invalid:{dependency.dependency_id}")

        ready = sorted(task_id for task_id, count in indegree.items() if count == 0)
        ordered: list[str] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for target in sorted(adjacency[current]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
                    ready.sort()
        if len(ordered) != len(tasks_by_id):
            issues.append("dependency_cycle_detected")
        return DependencyValidationResult(
            valid=not issues,
            reason_codes=tuple(sorted(set(issues))),
            topological_task_ids=tuple(ordered),
        )

    def releasable(
        self,
        *,
        target_task_id: str,
        tasks: Mapping[str, OrganizationTaskRef],
        dependencies: Iterable[OrganizationTaskDependency],
        verified_artifact_refs: Iterable[str] = (),
        satisfied_gate_ids: Iterable[str] = (),
    ) -> tuple[bool, tuple[str, ...]]:
        verified = set(verified_artifact_refs)
        gates = set(satisfied_gate_ids)
        blockers: list[str] = []
        for dependency in dependencies:
            if dependency.target_task_id != target_task_id:
                continue
            source = tasks.get(dependency.source_task_id)
            if source is None:
                blockers.append(f"source_task_missing:{dependency.dependency_id}")
                continue
            if source.status not in self.TERMINAL_SUCCESS:
                blockers.append(f"source_task_not_complete:{dependency.dependency_id}")
            for artifact_ref in dependency.required_artifact_refs:
                if artifact_ref not in verified:
                    blockers.append(f"artifact_not_verified:{dependency.dependency_id}:{artifact_ref}")
            if dependency.gate_id and dependency.gate_id not in gates:
                blockers.append(f"gate_not_satisfied:{dependency.dependency_id}:{dependency.gate_id}")
        return not blockers, tuple(sorted(set(blockers)))

    def reconcile_replaced_tasks(
        self,
        *,
        tasks: Mapping[str, OrganizationTaskRef],
        dependencies: Iterable[OrganizationTaskDependency],
    ) -> tuple[OrganizationTaskDependency, ...]:
        reconciled: list[OrganizationTaskDependency] = []
        for dependency in dependencies:
            source = tasks.get(dependency.source_task_id)
            target = tasks.get(dependency.target_task_id)
            next_dependency = dependency
            if source and source.status in self.TERMINAL_FAILURE:
                if source.replacement_task_id and source.replacement_task_id in tasks:
                    replacement = tasks[source.replacement_task_id]
                    next_dependency = replace(
                        next_dependency,
                        source_task_id=replacement.task_id,
                        source_team_id=replacement.team_id,
                        status="blocked",
                        blocking_reason="source_task_replaced",
                    )
                else:
                    next_dependency = replace(
                        next_dependency,
                        status="blocked",
                        blocking_reason="source_task_terminal_without_replacement",
                    )
            if target and target.status in self.TERMINAL_FAILURE:
                if target.replacement_task_id and target.replacement_task_id in tasks:
                    replacement = tasks[target.replacement_task_id]
                    next_dependency = replace(
                        next_dependency,
                        target_task_id=replacement.task_id,
                        target_team_id=replacement.team_id,
                        status="blocked",
                        blocking_reason="target_task_replaced",
                    )
                else:
                    next_dependency = replace(
                        next_dependency,
                        status="cancelled",
                        blocking_reason="target_task_terminal_without_replacement",
                    )
            reconciled.append(next_dependency)
        return tuple(reconciled)

    def status_projection(
        self,
        *,
        tasks: Iterable[OrganizationTaskRef],
        dependencies: Iterable[OrganizationTaskDependency],
    ) -> dict[str, object]:
        task_rows = tuple(tasks)
        dependency_rows = tuple(dependencies)
        by_team: dict[str, dict[str, int]] = {}
        for task in task_rows:
            bucket = by_team.setdefault(task.team_id, {})
            bucket[task.status] = bucket.get(task.status, 0) + 1
        blocked_targets = sorted(
            {row.target_task_id for row in dependency_rows if row.status in {"blocked", "pending"}}
        )
        statuses = {task.status for task in task_rows}
        if statuses and statuses <= self.TERMINAL_SUCCESS:
            organization_status = "completed"
        elif statuses & self.TERMINAL_FAILURE:
            organization_status = "failed"
        elif blocked_targets:
            organization_status = "blocked"
        elif "in_progress" in statuses or "running" in statuses:
            organization_status = "in_progress"
        else:
            organization_status = "pending"
        return {
            "organization_status": organization_status,
            "team_task_status_counts": by_team,
            "blocked_target_task_ids": blocked_targets,
        }


__all__ = [
    "DependencyValidationResult",
    "OrganizationDependencyService",
    "OrganizationTaskDependency",
    "OrganizationTaskRef",
]
