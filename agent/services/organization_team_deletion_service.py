"""Atomic early guard for deleting a legacy Team through the Hub."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agent.repositories.organization_team_deletion import (
    OrganizationTeamBinding,
    SqlOrganizationTeamDeletionUnitOfWork,
)
from agent.services.recovery_task_mutation_policy import (
    RecoveryTaskMutationConflict,
    ensure_external_recovery_mutation_allowed,
)


class OrganizationTeamDeletionError(RuntimeError):
    def __init__(self, reason_code: str, *, public_status: int, details: dict | None = None) -> None:
        self.reason_code = reason_code
        self.public_status = public_status
        self.details = dict(details or {})
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class OrganizationTeamDeletionPrincipal:
    principal_id: str
    tenant_id: str | None = None
    project_id: str | None = None
    is_hub_admin: bool = False


@dataclass(frozen=True, slots=True)
class OrganizationTeamDeletionResult:
    team_id: str
    deleted_members: int
    cleared_tasks: int
    cleared_goals: int


class OrganizationTeamDeletionService:
    """Guard and mutate a Team aggregate under one injected Unit of Work.

    The service depends on session-bound ports supplied by the UoW. It neither
    opens a Session nor commits individual repositories (DIP/SRP).
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlOrganizationTeamDeletionUnitOfWork] = SqlOrganizationTeamDeletionUnitOfWork,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._fault_injector = fault_injector or (lambda _step: None)

    def delete(
        self,
        *,
        team_id: str,
        principal: OrganizationTeamDeletionPrincipal,
    ) -> OrganizationTeamDeletionResult:
        normalized_team_id = str(team_id or "").strip()
        if not normalized_team_id:
            raise OrganizationTeamDeletionError("team_not_found", public_status=404)

        with self._uow_factory() as uow:
            team = uow.teams.lock_team(normalized_team_id)
            if team is None:
                raise OrganizationTeamDeletionError("team_not_found", public_status=404)

            bindings = uow.organization_links.lock_bindings(normalized_team_id)
            if bindings:
                # Authorization is evaluated before any link detail leaves the
                # service. A foreign/guessed Team remains indistinguishable
                # from a missing one.
                if not all(uow.authority.can_manage(principal=principal, binding=item) for item in bindings):
                    raise OrganizationTeamDeletionError("team_not_found", public_status=404)
                raise self._linked_team_conflict(bindings[0])

            tasks = uow.tasks.lock_for_team(normalized_team_id)
            try:
                for task in tasks:
                    ensure_external_recovery_mutation_allowed(task, action="team_delete")
            except RecoveryTaskMutationConflict as exc:
                raise OrganizationTeamDeletionError(
                    exc.reason_code,
                    public_status=409,
                    details=exc.as_data(),
                ) from exc
            goals = uow.goals.lock_for_team(normalized_team_id)
            members = uow.members.list_for_team(normalized_team_id)

            uow.members.delete_all(members)
            self._fault_injector("members_deleted")
            uow.tasks.clear_team(tasks)
            self._fault_injector("tasks_cleared")
            uow.goals.clear_team(goals)
            self._fault_injector("goals_cleared")
            late_bindings = uow.organization_links.lock_bindings(normalized_team_id)
            if late_bindings:
                if not all(uow.authority.can_manage(principal=principal, binding=item) for item in late_bindings):
                    raise OrganizationTeamDeletionError("team_not_found", public_status=404)
                raise self._linked_team_conflict(late_bindings[0])
            uow.teams.delete(team)
            self._fault_injector("team_deleted")
            uow.flush()

            return OrganizationTeamDeletionResult(
                team_id=normalized_team_id,
                deleted_members=len(members),
                cleared_tasks=len(tasks),
                cleared_goals=len(goals),
            )

    @staticmethod
    def _linked_team_conflict(binding: OrganizationTeamBinding) -> OrganizationTeamDeletionError:
        lifecycle = binding.organization_lifecycle
        if lifecycle in {"draft", "validated"}:
            next_step = "remove_team_via_organization_draft_patch"
        elif lifecycle in {"active", "paused"}:
            next_step = "drain_or_migrate_team_via_organization_lifecycle"
        else:
            next_step = "archive_team_link_via_organization_lifecycle"
        return OrganizationTeamDeletionError(
            "organization_team_delete_conflict",
            public_status=409,
            details={
                "organization_id": binding.organization_id,
                "organization_lifecycle": lifecycle,
                "team_link_lifecycle": binding.link_lifecycle,
                "next_step": next_step,
            },
        )


__all__ = [
    "OrganizationTeamDeletionError",
    "OrganizationTeamDeletionPrincipal",
    "OrganizationTeamDeletionResult",
    "OrganizationTeamDeletionService",
]
