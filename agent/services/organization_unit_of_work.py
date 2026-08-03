"""Single transaction boundary for all Organization aggregate writes."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType

from sqlmodel import Session

from agent.repositories.organizations import (
    SqlCrossTeamDependencyRepository,
    SqlOrganizationAdminGrantRepository,
    SqlOrganizationAssignmentRepository,
    SqlOrganizationAuditOutboxRepository,
    SqlOrganizationDefinitionImpactRepository,
    SqlOrganizationDefinitionRepository,
    SqlOrganizationGoalRepository,
    SqlOrganizationInstanceRepository,
    SqlOrganizationLayoutRepository,
    SqlOrganizationMembershipRepository,
    SqlOrganizationOperationRepository,
    SqlOrganizationRelationRepository,
    SqlOrganizationRoleSlotRepository,
    SqlOrganizationSnapshotRepository,
    SqlOrganizationTeamLinkRepository,
    SqlOrganizationTeamMaterializationRepository,
    SqlOrganizationTopologyPatchGrantRepository,
    SqlOrganizationTopologyReadRepository,
    SqlOrganizationUnitRepository,
)


class OrganizationUnitOfWork:
    """Own exactly one Session and commit or roll it back once on exit."""

    def __init__(self, *, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or self._default_session
        self.session: Session | None = None
        self._completed = False

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def __enter__(self) -> "OrganizationUnitOfWork":
        if self.session is not None:
            raise RuntimeError("organization_uow_already_entered")
        self.session = self._session_factory()
        self.definitions = SqlOrganizationDefinitionRepository(self.session)
        self.definition_impacts = SqlOrganizationDefinitionImpactRepository(self.session)
        self.instances = SqlOrganizationInstanceRepository(self.session)
        self.goals = SqlOrganizationGoalRepository(self.session)
        self.units = SqlOrganizationUnitRepository(self.session)
        self.team_links = SqlOrganizationTeamLinkRepository(self.session)
        self.teams = SqlOrganizationTeamMaterializationRepository(self.session)
        self.role_slots = SqlOrganizationRoleSlotRepository(self.session)
        self.assignments = SqlOrganizationAssignmentRepository(self.session)
        self.relations = SqlOrganizationRelationRepository(self.session)
        self.memberships = SqlOrganizationMembershipRepository(self.session)
        self.admin_grants = SqlOrganizationAdminGrantRepository(self.session)
        self.topology_patch_grants = SqlOrganizationTopologyPatchGrantRepository(self.session)
        self.layouts = SqlOrganizationLayoutRepository(self.session)
        self.snapshots = SqlOrganizationSnapshotRepository(self.session)
        self.dependencies = SqlCrossTeamDependencyRepository(self.session)
        self.operations = SqlOrganizationOperationRepository(self.session)
        self.audit_outbox = SqlOrganizationAuditOutboxRepository(self.session)
        self.topology = SqlOrganizationTopologyReadRepository(self.session)
        self._completed = False
        return self

    def flush(self) -> None:
        self._require_session().flush()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self.session
        if session is None:
            return
        try:
            if exc_type is None:
                try:
                    session.commit()
                except BaseException:
                    session.rollback()
                    raise
            else:
                session.rollback()
            self._completed = True
        finally:
            session.close()
            self.session = None

    def _require_session(self) -> Session:
        if self.session is None:
            raise RuntimeError("organization_uow_not_entered")
        return self.session


__all__ = ["OrganizationUnitOfWork"]
